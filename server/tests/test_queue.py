"""The Postgres job queue: claiming, retries with backoff, leases, and the reaper (PRD §12)."""

import asyncio
import random
from dataclasses import replace

import pytest

from tagmonitor.db.pool import Pool
from tagmonitor.queue.jobs import (
    PRIORITY_MANUAL,
    PRIORITY_SCHEDULED,
    Job,
    LostLease,
    backoff_seconds,
    claim,
    complete,
    enqueue,
    fail,
    heartbeat,
    reap,
    release,
)


async def job_row(pool: Pool, job_id: int) -> dict[str, object]:
    async with pool.connection() as conn:
        cursor = await conn.execute(
            "SELECT *, extract(epoch FROM run_at - now()) AS due_in_s FROM jobs WHERE id = %s",
            (job_id,),
        )
        row = await cursor.fetchone()
    assert row is not None
    return dict(row)


async def add(pool: Pool, n: int = 1, **kwargs: object) -> list[int]:
    ids = []
    async with pool.connection() as conn:
        for i in range(n):
            options = {"priority": PRIORITY_SCHEDULED} | kwargs
            job_id = await enqueue(conn, "retention", {"n": i}, **options)  # type: ignore[arg-type]
            assert job_id is not None
            ids.append(job_id)
    return ids


async def claim_one(pool: Pool, worker: str = "worker-a") -> Job:
    async with pool.connection() as conn:
        (job,) = await claim(conn, worker, 1)
    return job


# -- enqueue and claim ---------------------------------------------------------------------


async def test_dedupe_key_allows_one_active_job(db: Pool) -> None:
    async with db.connection() as conn:
        first = await enqueue(conn, "capture_and_check", {}, priority=10, dedupe_key="site:1")
        again = await enqueue(conn, "capture_and_check", {}, priority=10, dedupe_key="site:1")
    assert first is not None and again is None

    job = await claim_one(db)
    async with db.connection() as conn:
        # Still active while running...
        assert (
            await enqueue(conn, "capture_and_check", {}, priority=10, dedupe_key="site:1") is None
        )
        await complete(conn, job)
        # ...and free again once it finished.
        assert await enqueue(conn, "capture_and_check", {}, priority=10, dedupe_key="site:1")


async def test_claims_by_priority_then_age_and_skips_future_jobs(db: Pool) -> None:
    (old_low,) = await add(db, priority=PRIORITY_SCHEDULED)
    (urgent,) = await add(db, priority=PRIORITY_MANUAL)
    (new_low,) = await add(db, priority=PRIORITY_SCHEDULED)
    await add(db, priority=PRIORITY_MANUAL, delay_s=3600)  # not due yet

    async with db.connection() as conn:
        jobs = await claim(conn, "worker-a", 10)

    assert [j.id for j in jobs] == [urgent, old_low, new_low]
    assert all(j.attempts == 1 and j.locked_by == "worker-a" for j in jobs)


async def test_concurrent_claimers_never_get_the_same_job(db: Pool) -> None:
    """20 workers, each on its own connection, drain 400 jobs as fast as they can."""
    all_ids = set(await add(db, 400))
    claimed: list[int] = []

    async def drain(worker: str) -> None:
        async with db.connection() as conn:
            while jobs := await claim(conn, worker, 3):
                for job in jobs:
                    claimed.append(job.id)
                    await complete(conn, job)

    await asyncio.gather(*(drain(f"worker-{i}") for i in range(20)))

    assert len(claimed) == len(set(claimed)), "a job was claimed twice"
    assert set(claimed) == all_ids
    async with db.connection() as conn:
        cursor = await conn.execute("SELECT count(*) AS n FROM jobs WHERE attempts <> 1")
        row = await cursor.fetchone()
    assert row is not None and row["n"] == 0


# -- failure, backoff, release --------------------------------------------------------------


@pytest.mark.parametrize(
    ("attempts", "base"),
    [(1, 30), (2, 60), (3, 120), (4, 240), (6, 960), (7, 1800), (8, 1800), (20, 1800)],
)
def test_backoff_doubles_and_caps_at_30_minutes(attempts: int, base: float) -> None:
    rng = random.Random(7)
    delays = [backoff_seconds(attempts, rng) for _ in range(200)]
    assert all(0.8 * base <= d <= 1.2 * base for d in delays)
    assert max(delays) - min(delays) > 0.2 * base  # the jitter is really there


async def test_transient_failure_is_retried_after_backoff(db: Pool) -> None:
    await add(db)
    job = await claim_one(db)
    async with db.connection() as conn:
        assert await fail(conn, job, "timeout: page took too long", transient=True) == "queued"
    row = await job_row(db, job.id)
    assert row["status"] == "queued"
    assert row["attempts"] == 1  # the failed attempt counts
    assert row["locked_by"] is None
    assert row["last_error"] == "timeout: page took too long"
    assert 30 * 0.8 - 1 <= float(row["due_in_s"]) <= 30 * 1.2  # type: ignore[arg-type]


async def test_transient_failure_on_last_attempt_is_dead(db: Pool) -> None:
    await add(db)
    job = await claim_one(db)
    last_try = replace(job, attempts=job.max_attempts)
    async with db.connection() as conn:
        await conn.execute("UPDATE jobs SET attempts = max_attempts WHERE id = %s", (job.id,))
        assert await fail(conn, last_try, "timeout", transient=True) == "dead"
    row = await job_row(db, job.id)
    assert row["status"] == "dead" and row["finished_at"] is not None


async def test_permanent_failure_is_dead_immediately(db: Pool) -> None:
    await add(db)
    job = await claim_one(db)
    async with db.connection() as conn:
        assert await fail(conn, job, "ssrf_blocked", transient=False) == "dead"
    assert (await job_row(db, job.id))["last_error"] == "ssrf_blocked"


async def test_release_gives_the_attempt_back(db: Pool) -> None:
    await add(db)
    job = await claim_one(db)
    async with db.connection() as conn:
        await release(conn, job, delay_s=20, reason="domain busy")
    row = await job_row(db, job.id)
    assert row["status"] == "queued" and row["attempts"] == 0
    assert 18 <= float(row["due_in_s"]) <= 20  # type: ignore[arg-type]


# -- leases, heartbeats and the reaper --------------------------------------------------------


async def make_stale(pool: Pool, job_id: int) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE jobs SET heartbeat_at = now() - interval '5 minutes' WHERE id = %s", (job_id,)
        )


async def test_reaper_requeues_jobs_of_dead_workers(db: Pool) -> None:
    await add(db, 2)
    stale = await claim_one(db, "crashed-worker")
    alive = await claim_one(db, "healthy-worker")
    await make_stale(db, stale.id)

    async with db.connection() as conn:
        assert await reap(conn, stale_after_s=120) == {"queued": 1, "dead": 0}

    row = await job_row(db, stale.id)
    assert row["status"] == "queued" and row["attempts"] == 1  # the crash counted
    assert row["last_error"] == "worker stopped heartbeating; reaped"
    assert (await job_row(db, alive.id))["status"] == "running"


async def test_reaper_kills_poison_pills(db: Pool) -> None:
    await add(db)
    job = await claim_one(db)
    async with db.connection() as conn:
        await conn.execute("UPDATE jobs SET attempts = max_attempts WHERE id = %s", (job.id,))
    await make_stale(db, job.id)
    async with db.connection() as conn:
        assert await reap(conn, stale_after_s=120) == {"queued": 0, "dead": 1}


async def test_heartbeats_keep_a_job_alive(db: Pool) -> None:
    await add(db)
    job = await claim_one(db)
    await make_stale(db, job.id)
    async with db.connection() as conn:
        assert await heartbeat(conn, [job]) == 1
        assert await reap(conn, stale_after_s=120) == {"queued": 0, "dead": 0}


async def test_a_worker_that_lost_its_lease_cannot_touch_the_job(db: Pool) -> None:
    """Worker A stalls, the reaper hands the job to worker B, then A wakes up."""
    await add(db)
    job_a = await claim_one(db, "worker-a")
    await make_stale(db, job_a.id)
    async with db.connection() as conn:
        await reap(conn, stale_after_s=120)
    job_b = await claim_one(db, "worker-b")
    assert job_b.id == job_a.id and job_b.attempts == 2

    async with db.connection() as conn:
        with pytest.raises(LostLease):
            await complete(conn, job_a)
        with pytest.raises(LostLease):
            await fail(conn, job_a, "late", transient=True)
        assert await heartbeat(conn, [job_a]) == 0
        await complete(conn, job_b)  # the real owner is unaffected

    row = await job_row(db, job_a.id)
    assert row["status"] == "succeeded" and row["locked_by"] == "worker-b"
