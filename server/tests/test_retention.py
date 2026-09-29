"""The retention job (PRD §11): old monitoring history goes, in batches, with its stored
objects; the latest state, scans, alerts and cost history stay."""

from datetime import timedelta
from typing import Any
from uuid import UUID

from tagmonitor.db.pool import Pool
from tagmonitor.queue.jobs import enqueue
from tagmonitor.retention import purge
from tagmonitor.scheduler import schedule_retention
from tagmonitor.storage import ObjectStorage
from tests.factories import insert_site, insert_user
from tests.test_worker import job_status, make_worker, run_until, status_is


async def add_run(
    db: Pool,
    storage: ObjectStorage,
    *,
    site_id: UUID | None,
    days_ago: float,
    device: str = "mobile",
    scan_id: UUID | None = None,
) -> tuple[int, list[str]]:
    """A run from `days_ago`, with a result and a capture + screenshot in storage."""
    async with db.connection() as conn:
        cursor = await conn.execute(
            "INSERT INTO check_runs (site_id, scan_id, device, started_at, status) "
            "VALUES (%s, %s, %s, now() - %s, 'completed') RETURNING id",
            (site_id, scan_id, device, timedelta(days=days_ago)),
        )
        row = await cursor.fetchone()
        assert row is not None
        run_id = int(row["id"])
        keys = [f"retention-test/{run_id}.json", f"retention-test/{run_id}.jpg"]
        await conn.execute(
            "UPDATE check_runs SET capture_key = %s, screenshot_key = %s WHERE id = %s",
            (*keys, run_id),
        )
        await conn.execute(
            "INSERT INTO check_results (run_id, check_key, status, code, summary) "
            "VALUES (%s, 'page_health', 'pass', 'ok', 'fine')",
            (run_id,),
        )
    for key in keys:
        await storage.put(key, b"x", "application/octet-stream")
    return run_id, keys


async def run_ids(db: Pool) -> set[int]:
    async with db.connection() as conn:
        cursor = await conn.execute("SELECT id FROM check_runs")
        return {row["id"] for row in await cursor.fetchall()}


async def exists(storage: ObjectStorage, key: str) -> bool:
    try:
        await storage.get(key)
    except Exception:  # NoSuchKey, from boto3
        return False
    return True


async def test_old_runs_and_their_objects_go_but_the_latest_state_stays(
    db: Pool, storage: ObjectStorage
) -> None:
    user = await insert_user(db)
    active = await insert_site(db, user, "https://active.example/")
    paused = await insert_site(db, user, "https://paused.example/", paused=True)

    old_active, old_keys = await add_run(db, storage, site_id=active, days_ago=120)
    recent_active, _ = await add_run(db, storage, site_id=active, days_ago=1)
    # The paused site's history is all old; its newest run per device must survive.
    oldest_paused, _ = await add_run(db, storage, site_id=paused, days_ago=200)
    last_mobile, kept_keys = await add_run(db, storage, site_id=paused, days_ago=150)
    last_desktop, _ = await add_run(db, storage, site_id=paused, days_ago=150, device="desktop")
    async with db.connection() as conn:
        cursor = await conn.execute("INSERT INTO scans (name) VALUES ('old-scan') RETURNING id")
        scan = await cursor.fetchone()
    assert scan is not None
    scan_run, _ = await add_run(db, storage, site_id=None, scan_id=scan["id"], days_ago=365)

    report = await purge(db, storage, days=90, batch_size=1)  # batch of 1: exercise the loop

    assert report.runs == 2 and report.objects == 4
    assert await run_ids(db) == {recent_active, last_mobile, last_desktop, scan_run}
    assert old_active not in await run_ids(db) and oldest_paused not in await run_ids(db)
    assert not any([await exists(storage, key) for key in old_keys])
    assert all([await exists(storage, key) for key in kept_keys])
    async with db.connection() as conn:  # results went with their runs (ON DELETE CASCADE)
        cursor = await conn.execute("SELECT count(*) AS n FROM check_results")
        assert await cursor.fetchone() == {"n": 4}


async def test_other_tables(db: Pool, storage: ObjectStorage) -> None:
    user = await insert_user(db)
    site = await insert_site(db, user)
    statements: list[tuple[str, tuple[Any, ...]]] = [
        (
            "INSERT INTO jobs (type, status, finished_at) VALUES "
            "('capture_and_check', 'succeeded', now() - interval '100 days'), "
            "('capture_and_check', 'dead', now() - interval '100 days'), "
            "('capture_and_check', 'succeeded', now() - interval '1 day')",
            (),
        ),
        (
            "INSERT INTO jobs (type, status, created_at) VALUES "
            "('capture_and_check', 'queued', now() - interval '100 days')",
            (),
        ),
        (
            "INSERT INTO sessions (user_id, token_hash, expires_at) VALUES "
            "(%s, 'a', now() - interval '1 minute'), (%s, 'b', now() + interval '1 day')",
            (user, user),
        ),
        (
            "INSERT INTO login_attempts (key, attempted_at) VALUES "
            "('x', now() - interval '2 days'), ('y', now())",
            (),
        ),
        (
            "INSERT INTO llm_cache (cache_key, model, prompt_version, response, created_at) "
            "VALUES ('old', 'm', 'v1', '{}', now() - interval '100 days'), "
            "('new', 'm', 'v1', '{}', now())",
            (),
        ),
        (
            "INSERT INTO llm_usage (created_at, model, purpose, input_tokens, output_tokens) "
            "VALUES (now() - interval '400 days', 'm', 'eval', 1, 1)",
            (),
        ),
        (
            "INSERT INTO alerts (site_id, check_key, kind, dedupe_key, subject, body_text, "
            "body_html, created_at) VALUES (%s, 'meta_pixel', 'failure', 'k', 's', 't', 'h', "
            "now() - interval '400 days')",
            (site,),
        ),
    ]
    async with db.connection() as conn:
        for statement, params in statements:
            await conn.execute(statement, params)

    report = await purge(db, storage, days=90)

    assert report.other == {"jobs": 2, "sessions": 1, "login_attempts": 1, "llm_cache": 1}
    async with db.connection() as conn:
        counts = {}
        for table in ("jobs", "sessions", "login_attempts", "llm_cache", "llm_usage", "alerts"):
            cursor = await conn.execute(f"SELECT count(*) AS n FROM {table}")  # noqa: S608
            row = await cursor.fetchone()
            assert row is not None
            counts[table] = row["n"]
    # Kept: the recent and the still-queued job, the live session, the fresh attempt and
    # cache entry, and all cost history and alert history.
    assert counts == {
        "jobs": 2,
        "sessions": 1,
        "login_attempts": 1,
        "llm_cache": 1,
        "llm_usage": 1,
        "alerts": 1,
    }


async def test_scheduler_enqueues_retention_once_a_day(db: Pool) -> None:
    async with db.connection() as conn:
        assert await schedule_retention(conn) is True
        assert await schedule_retention(conn) is False  # one is already queued
        await conn.execute(
            "UPDATE jobs SET status = 'succeeded', finished_at = now() - interval '1 hour'"
        )
        assert await schedule_retention(conn) is False  # ran an hour ago
        await conn.execute("UPDATE jobs SET finished_at = now() - interval '25 hours'")
        assert await schedule_retention(conn) is True


async def test_worker_runs_the_retention_job(db: Pool, storage: ObjectStorage) -> None:
    user = await insert_user(db)
    site = await insert_site(db, user)
    old, _ = await add_run(db, storage, site_id=site, days_ago=100)  # default: 90 days
    await add_run(db, storage, site_id=site, days_ago=1)
    async with db.connection() as conn:
        job_id = await enqueue(conn, "retention", {}, priority=5, dedupe_key="retention")
    assert job_id is not None

    await run_until([make_worker(db, None, storage)], status_is(db, job_id, "succeeded"))
    assert old not in await run_ids(db)
    assert (await job_status(db, job_id))["status"] == "succeeded"
