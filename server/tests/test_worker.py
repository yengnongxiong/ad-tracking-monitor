"""Workers end to end: real captures, MinIO, the scheduler, two workers, failures, shutdown."""

import asyncio
from collections import Counter
from collections.abc import Awaitable, Callable
from typing import cast
from uuid import UUID

from tagmonitor.alerts.senders import ConsoleEmailSender, EmailSender
from tagmonitor.browser.capturer import CaptureError, CaptureResult, PageCapturer
from tagmonitor.config import Settings, get_settings
from tagmonitor.db.pool import Pool
from tagmonitor.page_capture import PageCapture
from tagmonitor.queue.jobs import PRIORITY_MANUAL, Job, complete, enqueue
from tagmonitor.storage import ObjectStorage
from tagmonitor.worker.__main__ import DEAD_HANDLERS, HANDLERS
from tagmonitor.worker.context import Handler, WorkerContext
from tagmonitor.worker.runner import Timings, Worker
from tests.factories import insert_site, insert_user
from tests.fixture_server import FixtureServer

FAST = Timings(poll_s=0.05, heartbeat_s=0.2, scheduler_s=0.2, reaper_s=0.2, shutdown_grace_s=5)


def make_worker(
    pool: Pool,
    capturer: object,
    storage: ObjectStorage,
    *,
    worker_id: str = "worker-1",
    handlers: dict[str, Handler] | None = None,
    timings: Timings = FAST,
    run_scheduler: bool = False,
    email: EmailSender | None = None,
    settings: Settings | None = None,
) -> Worker:
    context = WorkerContext(
        pool=pool,
        capturer=cast(PageCapturer, capturer),
        storage=storage,
        email=email or ConsoleEmailSender(),
        settings=settings or get_settings(),
    )
    return Worker(
        context,
        worker_id=worker_id,
        concurrency=2,
        handlers=HANDLERS if handlers is None else handlers,
        dead_handlers=DEAD_HANDLERS,
        timings=timings,
        run_scheduler=run_scheduler,
    )


async def run_until(
    workers: list[Worker], done: Callable[[], Awaitable[bool]], timeout_s: float = 90
) -> None:
    stop = asyncio.Event()
    tasks = [asyncio.create_task(w.run(stop)) for w in workers]
    try:
        async with asyncio.timeout(timeout_s):
            # Polling on purpose: the condition is database state, not an in-process event.
            while not await done():  # noqa: ASYNC110
                await asyncio.sleep(0.1)
    finally:
        stop.set()
        await asyncio.gather(*tasks)


async def job_status(pool: Pool, job_id: int) -> dict[str, object]:
    async with pool.connection() as conn:
        cursor = await conn.execute("SELECT * FROM jobs WHERE id = %s", (job_id,))
        row = await cursor.fetchone()
    assert row is not None
    return dict(row)


def status_is(pool: Pool, job_id: int, *statuses: str) -> Callable[[], Awaitable[bool]]:
    async def check() -> bool:
        return (await job_status(pool, job_id))["status"] in statuses

    return check


async def add_site(pool: Pool, server: FixtureServer, fixture: str, **columns: object) -> UUID:
    user = await insert_user(pool)
    return await insert_site(
        pool, user, server.url(fixture), registrable_domain="fixtures.test", **columns
    )


async def enqueue_capture(pool: Pool, site: UUID, reason: str = "manual") -> int:
    async with pool.connection() as conn:
        job_id = await enqueue(
            conn,
            "capture_and_check",
            {"site_id": str(site), "reason": reason},
            priority=PRIORITY_MANUAL,
            dedupe_key=f"site:{site}",
        )
    assert job_id is not None
    return job_id


async def test_capture_job_stores_runs_results_and_evidence(
    db: Pool, capturer: PageCapturer, storage: ObjectStorage, fixture_server: FixtureServer
) -> None:
    site = await add_site(db, fixture_server, "meta_ok")
    job_id = await enqueue_capture(db, site)

    await run_until([make_worker(db, capturer, storage)], status_is(db, job_id, "succeeded"))

    async with db.connection() as conn:
        cursor = await conn.execute(
            "SELECT * FROM check_runs WHERE site_id = %s ORDER BY device DESC", (site,)
        )
        runs = await cursor.fetchall()
        cursor = await conn.execute(
            "SELECT r.device, c.check_key, c.status, c.code FROM check_results c "
            "JOIN check_runs r ON r.id = c.run_id"
        )
        results = await cursor.fetchall()

    assert [(r["device"], r["status"], r["http_status"]) for r in runs] == [
        ("mobile", "completed", 200),
        ("desktop", "completed", 200),
    ]
    assert Counter(r["device"] for r in results) == {"mobile": 7, "desktop": 5}
    meta = {
        (r["device"], r["status"], r["code"]) for r in results if r["check_key"] == "meta_pixel"
    }
    assert meta == {("mobile", "pass", "firing"), ("desktop", "pass", "firing")}

    # The evidence landed in MinIO, and the stored capture points at its screenshot.
    for run in runs:
        capture = PageCapture.model_validate_json(await storage.get(run["capture_key"]))
        assert capture.screenshot_key == run["screenshot_key"]
        assert (await storage.get(run["screenshot_key"]))[:2] == b"\xff\xd8"


async def test_scheduler_and_worker_together(
    db: Pool, capturer: PageCapturer, storage: ObjectStorage, fixture_server: FixtureServer
) -> None:
    site = await add_site(db, fixture_server, "no_tags")  # next_check_at defaults to now

    async def ran() -> bool:
        async with db.connection() as conn:
            cursor = await conn.execute(
                "SELECT count(*) AS n FROM jobs WHERE status = 'succeeded' "
                "AND payload->>'reason' = 'scheduled' AND payload->>'site_id' = %s",
                (str(site),),
            )
            row = await cursor.fetchone()
        return row is not None and row["n"] == 1

    await run_until([make_worker(db, capturer, storage, run_scheduler=True)], ran)


async def test_two_workers_share_one_queue(db: Pool, storage: ObjectStorage) -> None:
    processed: list[tuple[int, str]] = []

    def recording_handler(worker_id: str) -> Handler:
        async def handle(job: Job, ctx: WorkerContext) -> None:
            await asyncio.sleep(0.05)  # "work"
            processed.append((job.id, worker_id))
            async with ctx.pool.connection() as conn:
                await complete(conn, job)

        return handle

    async with db.connection() as conn:
        job_ids = [await enqueue(conn, "retention", {"n": i}, priority=0) for i in range(30)]

    workers = [
        make_worker(db, None, storage, worker_id=w, handlers={"retention": recording_handler(w)})
        for w in ("worker-1", "worker-2")
    ]

    async def all_done() -> bool:
        return len(processed) == len(job_ids)

    await run_until(workers, all_done)
    assert sorted(job_id for job_id, _ in processed) == sorted(job_ids)  # each exactly once
    assert {worker for _, worker in processed} == {"worker-1", "worker-2"}


async def test_busy_domain_releases_the_job_without_using_an_attempt(
    db: Pool, capturer: PageCapturer, storage: ObjectStorage, fixture_server: FixtureServer
) -> None:
    site = await add_site(db, fixture_server, "no_tags")
    async with db.connection() as holder:  # another worker is on fixtures.test right now
        await holder.execute("SELECT pg_advisory_lock(hashtextextended('fixtures.test', 0))")
        job_id = await enqueue_capture(db, site)

        async def released() -> bool:
            row = await job_status(db, job_id)
            return row["status"] == "queued" and row["last_error"] is not None

        await run_until([make_worker(db, capturer, storage)], released)
        await holder.execute("SELECT pg_advisory_unlock(hashtextextended('fixtures.test', 0))")

    row = await job_status(db, job_id)
    assert row["attempts"] == 0
    assert str(row["last_error"]).startswith("domain busy")


async def test_a_redirect_to_a_private_address_kills_the_job_and_leaves_a_trace(
    db: Pool, capturer: PageCapturer, storage: ObjectStorage, fixture_server: FixtureServer
) -> None:
    site = await add_site(db, fixture_server, "redirect_to_private_ip")
    job_id = await enqueue_capture(db, site)

    await run_until([make_worker(db, capturer, storage)], status_is(db, job_id, "dead"))

    assert (await job_status(db, job_id))["attempts"] == 1  # permanent: no retries
    async with db.connection() as conn:
        cursor = await conn.execute(
            "SELECT status, error_code FROM check_runs WHERE site_id = %s", (site,)
        )
        runs = await cursor.fetchall()
    assert [(r["status"], r["error_code"]) for r in runs] == [("error", "ssrf_blocked")]


class FlakyCapturer:
    """Fails like a crashed browser would."""

    async def capture(self, url: str, device: str) -> CaptureResult:
        raise CaptureError("browser_crash", "Target closed", transient=True)


async def test_transient_failures_are_retried_later(
    db: Pool, storage: ObjectStorage, fixture_server: FixtureServer
) -> None:
    site = await add_site(db, fixture_server, "no_tags")
    job_id = await enqueue_capture(db, site)

    async def retried() -> bool:
        row = await job_status(db, job_id)
        return row["status"] == "queued" and row["attempts"] == 1

    await run_until([make_worker(db, FlakyCapturer(), storage)], retried)
    assert str((await job_status(db, job_id))["last_error"]).startswith("browser_crash")


async def test_shutdown_hands_unfinished_jobs_back(db: Pool, storage: ObjectStorage) -> None:
    async def never_finishes(job: Job, ctx: WorkerContext) -> None:
        await asyncio.sleep(3600)

    async with db.connection() as conn:
        job_id = await enqueue(conn, "retention", {}, priority=0)
    assert job_id is not None
    worker = make_worker(
        db,
        None,
        storage,
        handlers={"retention": never_finishes},
        timings=Timings(poll_s=0.05, shutdown_grace_s=0.2),
    )

    await run_until([worker], status_is(db, job_id, "running"))  # stop as soon as it started

    row = await job_status(db, job_id)
    assert row["status"] == "queued"
    assert row["attempts"] == 0  # the interrupted attempt doesn't count
    assert row["last_error"] == "worker shut down before finishing"


async def test_scheduled_job_for_a_paused_site_is_skipped(
    db: Pool, storage: ObjectStorage, fixture_server: FixtureServer
) -> None:
    site = await add_site(db, fixture_server, "no_tags", paused=True)
    job_id = await enqueue_capture(db, site, reason="scheduled")

    # FlakyCapturer proves no capture was attempted: the job would have failed otherwise.
    await run_until([make_worker(db, FlakyCapturer(), storage)], status_is(db, job_id, "succeeded"))


async def test_unknown_job_types_go_dead(db: Pool, storage: ObjectStorage) -> None:
    async with db.connection() as conn:
        job_id = await enqueue(conn, "scan_url", {}, priority=0)
    assert job_id is not None
    await run_until([make_worker(db, None, storage)], status_is(db, job_id, "dead"))
    assert str((await job_status(db, job_id))["last_error"]).startswith("unknown_job_type")
