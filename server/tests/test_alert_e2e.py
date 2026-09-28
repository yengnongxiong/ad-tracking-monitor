"""M5 acceptance, end to end: a pixel breaks, gets confirmed, the owner is emailed (via
Mailpit), the pixel is fixed, and a recovery email follows. Real browser, real worker, real SMTP.
"""

import asyncio
from uuid import UUID, uuid4

from tagmonitor.alerts.senders import SmtpEmailSender
from tagmonitor.browser.capturer import PageCapturer
from tagmonitor.config import get_settings
from tagmonitor.db.pool import Pool
from tagmonitor.queue.jobs import PRIORITY_MANUAL, enqueue
from tagmonitor.storage import ObjectStorage
from tests import mailpit
from tests.factories import insert_site, insert_user
from tests.fixture_server import FixtureServer
from tests.test_worker import make_worker, run_until


async def point_site_at(pool: Pool, site: UUID, url: str) -> None:
    """Stand-in for the owner's site changing: point the site at another fixture page."""
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE sites SET url = %s, normalized_url = %s WHERE id = %s", (url, url, site)
        )


async def check_now(pool: Pool, site: UUID) -> None:
    """Like the "Check now" button: wait for any in-flight job for the site, then enqueue."""
    async with asyncio.timeout(60):
        while True:
            async with pool.connection() as conn:
                job_id = await enqueue(
                    conn,
                    "capture_and_check",
                    {"site_id": str(site), "reason": "manual"},
                    priority=PRIORITY_MANUAL,
                    dedupe_key=f"site:{site}",
                )
            if job_id is not None:
                return
            await asyncio.sleep(0.2)


async def meta_state(pool: Pool, site: UUID) -> str | None:
    async with pool.connection() as conn:
        cursor = await conn.execute(
            "SELECT state FROM site_check_states WHERE site_id = %s AND check_key = 'meta_pixel'",
            (site,),
        )
        row = await cursor.fetchone()
    return None if row is None else str(row["state"])


async def test_break_confirm_alert_fix_recover(
    db: Pool, capturer: PageCapturer, storage: ObjectStorage, fixture_server: FixtureServer
) -> None:
    owner = f"{uuid4().hex[:10]}@test.example"
    site = await insert_site(
        db,
        await insert_user(db),
        fixture_server.url("meta_ok"),
        registrable_domain="fixtures.test",
        alert_email=owner,
        name="Bean There",
    )
    settings = get_settings().model_copy(update={"confirm_delay_seconds": 0})
    worker = make_worker(db, capturer, storage, email=SmtpEmailSender(settings), settings=settings)

    async def meta_mail(prefix: str) -> bool:
        return any(m["Subject"].startswith(prefix) for m in await mailpit.messages_to(owner))

    async def worker_until(condition: object) -> None:
        await run_until([worker], condition, timeout_s=120)  # type: ignore[arg-type]

    # 1. First check: the pixel works.
    await check_now(db, site)

    async def healthy() -> bool:
        return await meta_state(db, site) == "healthy"

    await worker_until(healthy)

    # 2. The pixel breaks. The first failure only makes the check "suspect"; the confirmation
    #    re-check (delay 0 here, 10 minutes by default) confirms it and the email goes out.
    await point_site_at(db, site, fixture_server.url("meta_not_firing"))
    await check_now(db, site)
    await worker_until(lambda: meta_mail("Your Meta Pixel stopped firing on fixtures.test"))

    # 3. The pixel is fixed: one recovery email.
    await point_site_at(db, site, fixture_server.url("meta_ok"))
    await check_now(db, site)
    await worker_until(lambda: meta_mail("Resolved: Meta Pixel on fixtures.test"))

    subjects = [m["Subject"] for m in await mailpit.messages_to(owner)]
    meta_subjects = [s for s in subjects if "Meta Pixel" in s]
    assert sorted(meta_subjects) == [
        "Resolved: Meta Pixel on fixtures.test",
        "Your Meta Pixel stopped firing on fixtures.test",
    ]
