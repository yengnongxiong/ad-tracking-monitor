"""The scheduler turns due sites into capture jobs exactly once (PRD §12)."""

from uuid import UUID

from tagmonitor.db.pool import Pool
from tagmonitor.scheduler import SCHEDULER_LOCK, schedule_due_sites
from tests.factories import insert_job, insert_site, insert_user


async def make_due(pool: Pool, site_id: UUID) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE sites SET next_check_at = now() - interval '1 minute' WHERE id = %s",
            (site_id,),
        )


async def tick(pool: Pool) -> int:
    async with pool.connection() as conn:
        return await schedule_due_sites(conn)


async def capture_jobs(pool: Pool) -> list[dict[str, object]]:
    async with pool.connection() as conn:
        cursor = await conn.execute(
            "SELECT payload, priority, dedupe_key FROM jobs WHERE type = 'capture_and_check'"
        )
        return [dict(row) for row in await cursor.fetchall()]


async def test_enqueues_due_sites_once_and_reschedules_them(db: Pool) -> None:
    user = await insert_user(db)
    site = await insert_site(db, user, check_interval_minutes=60)
    await make_due(db, site)

    assert await tick(db) == 1
    assert await tick(db) == 0  # no longer due

    (job,) = await capture_jobs(db)
    assert job["payload"] == {"site_id": str(site), "reason": "scheduled"}
    assert job["priority"] == 10
    assert job["dedupe_key"] == f"site:{site}"
    async with db.connection() as conn:
        cursor = await conn.execute(
            "SELECT extract(epoch FROM next_check_at - now()) / 60 AS minutes FROM sites"
        )
        row = await cursor.fetchone()
    assert row is not None
    assert 60 * 0.95 - 0.1 <= float(row["minutes"]) <= 60 * 1.05  # ±5% jitter


async def test_skips_paused_and_not_yet_due_sites(db: Pool) -> None:
    user = await insert_user(db)
    paused = await insert_site(db, user, "https://a.example.com/", paused=True)
    await make_due(db, paused)
    await insert_site(db, user, "https://b.example.com/")  # next_check_at defaults to now()
    async with db.connection() as conn:
        await conn.execute(
            "UPDATE sites SET next_check_at = now() + interval '1 hour' WHERE NOT paused"
        )

    assert await tick(db) == 0
    assert await capture_jobs(db) == []


async def test_does_not_duplicate_a_job_already_queued(db: Pool) -> None:
    user = await insert_user(db)
    site = await insert_site(db, user)
    await insert_job(db, dedupe_key=f"site:{site}", status="queued")  # e.g. a confirm check
    await make_due(db, site)

    assert await tick(db) == 0
    assert len(await capture_jobs(db)) == 1
    async with db.connection() as conn:  # ...but the site is still pushed to its next slot
        cursor = await conn.execute("SELECT next_check_at > now() AS moved FROM sites")
        row = await cursor.fetchone()
    assert row is not None and row["moved"]


async def test_only_one_scheduler_ticks_at_a_time(db: Pool) -> None:
    user = await insert_user(db)
    await make_due(db, await insert_site(db, user))
    async with db.connection() as holder, holder.transaction():
        await holder.execute("SELECT pg_advisory_xact_lock(%s, %s)", SCHEDULER_LOCK)
        assert await tick(db) == 0  # the other "worker" holds the scheduler lock
    assert await tick(db) == 1
