"""A job queue in Postgres (PRD §12, ADR-010).

Workers claim jobs with UPDATE ... FOR UPDATE SKIP LOCKED: concurrent claimers skip rows
another transaction has locked instead of waiting, so no job is handed out twice and nobody
blocks. A claimed job is "leased" to one worker, identified by (id, locked_by, attempts).
Every later write checks that whole fencing token, so a worker that stalled long enough for
the reaper to hand its job to someone else can't complete, fail or release the job twice.
"""

import json
import random
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

type Conn = AsyncConnection[DictRow]
type JobType = Literal["capture_and_check", "send_alert", "scan_url", "retention"]

# Higher runs first (PRD §12).
PRIORITY_MANUAL = 100
PRIORITY_CONFIRM = 50
PRIORITY_SEND_ALERT = 40
PRIORITY_SCHEDULED = 10
PRIORITY_SCAN = 0

BACKOFF_BASE_S = 30.0
BACKOFF_MAX_S = 30 * 60.0
BACKOFF_JITTER = 0.2
_rng = random.Random()  # noqa: S311 (jitter, not security)


@dataclass(frozen=True)
class Job:
    id: int
    type: str
    payload: dict[str, Any]
    priority: int
    attempts: int
    max_attempts: int
    locked_by: str | None
    run_at: datetime
    dedupe_key: str | None

    @classmethod
    def from_row(cls, row: DictRow) -> "Job":
        payload = row["payload"]
        return cls(
            id=row["id"],
            type=row["type"],
            payload=payload if isinstance(payload, dict) else json.loads(payload),
            priority=row["priority"],
            attempts=row["attempts"],
            max_attempts=row["max_attempts"],
            locked_by=row["locked_by"],
            run_at=row["run_at"],
            dedupe_key=row["dedupe_key"],
        )


class LostLease(Exception):
    """The job is no longer ours: the reaper gave it to another worker while we held it."""


def backoff_seconds(attempts: int, rng: random.Random | None = None) -> float:
    """Delay before retry number `attempts`: 30 s, 60 s, 120 s ... capped at 30 min, ±20%.

    Exponential so a struggling site or service gets breathing room; capped so a job is
    never parked for hours; jittered so jobs that failed together don't retry together.
    """
    base = min(BACKOFF_BASE_S * 2.0 ** max(attempts - 1, 0), BACKOFF_MAX_S)
    return base * (rng or _rng).uniform(1 - BACKOFF_JITTER, 1 + BACKOFF_JITTER)


async def enqueue(
    conn: Conn,
    type: JobType,
    payload: dict[str, Any],
    *,
    priority: int,
    dedupe_key: str | None = None,
    delay_s: float = 0,
) -> int | None:
    """Add a job. Returns its id, or None when an active job with the same dedupe key exists."""
    cursor = await conn.execute(
        """
        INSERT INTO jobs (type, payload, priority, dedupe_key, run_at)
        VALUES (%s, %s, %s, %s, now() + make_interval(secs => %s))
        ON CONFLICT (dedupe_key) WHERE status IN ('queued', 'running') DO NOTHING
        RETURNING id
        """,
        (type, Jsonb(payload), priority, dedupe_key, delay_s),
    )
    row = await cursor.fetchone()
    return None if row is None else int(row["id"])


async def claim(conn: Conn, worker_id: str, limit: int) -> list[Job]:
    """Lease up to `limit` due jobs, highest priority first (PRD §12's single statement)."""
    cursor = await conn.execute(
        """
        UPDATE jobs SET status = 'running', locked_by = %s, locked_at = now(),
               heartbeat_at = now(), attempts = attempts + 1
        WHERE id IN (
            SELECT id FROM jobs
            WHERE status = 'queued' AND run_at <= now()
            ORDER BY priority DESC, run_at
            FOR UPDATE SKIP LOCKED
            LIMIT %s)
        RETURNING *
        """,
        (worker_id, limit),
    )
    jobs = [Job.from_row(row) for row in await cursor.fetchall()]
    # RETURNING has no ORDER BY; keep the claim order so the most urgent job starts first.
    return sorted(jobs, key=lambda job: (-job.priority, job.run_at))


_FENCE = "id = %(id)s AND locked_by = %(worker)s AND attempts = %(attempts)s AND status = 'running'"


def _fence(job: Job) -> dict[str, Any]:
    return {"id": job.id, "worker": job.locked_by, "attempts": job.attempts}


async def _update_fenced(conn: Conn, set_clause: str, job: Job, **params: Any) -> None:
    # Both SQL fragments are constants from this module; only values are parameters.
    cursor = await conn.execute(
        f"UPDATE jobs SET {set_clause} WHERE {_FENCE}",  # noqa: S608
        _fence(job) | params,
    )
    if cursor.rowcount != 1:
        raise LostLease(f"job {job.id} is no longer leased to {job.locked_by}")


async def complete(conn: Conn, job: Job) -> None:
    await _update_fenced(conn, "status = 'succeeded', finished_at = now()", job)


async def fail(conn: Conn, job: Job, error: str, *, transient: bool) -> str:
    """Record a failure. Transient failures with attempts left are retried after a backoff;
    everything else is dead. Returns the new status ('queued' or 'dead')."""
    if transient and job.attempts < job.max_attempts:
        await _update_fenced(
            conn,
            "status = 'queued', run_at = now() + make_interval(secs => %(delay)s), "
            "locked_by = NULL, locked_at = NULL, heartbeat_at = NULL, last_error = %(error)s",
            job,
            delay=backoff_seconds(job.attempts),
            error=error[:2000],
        )
        return "queued"
    await _update_fenced(
        conn,
        "status = 'dead', finished_at = now(), last_error = %(error)s",
        job,
        error=error[:2000],
    )
    return "dead"


async def release(conn: Conn, job: Job, *, delay_s: float, reason: str) -> None:
    """Put a job back without counting the attempt: we never started it (the domain was busy)
    or we're shutting down mid-way. The work simply happens later."""
    await _update_fenced(
        conn,
        "status = 'queued', run_at = now() + make_interval(secs => %(delay)s), "
        "attempts = attempts - 1, locked_by = NULL, locked_at = NULL, heartbeat_at = NULL, "
        "last_error = %(reason)s",
        job,
        delay=delay_s,
        reason=reason,
    )


async def heartbeat(conn: Conn, jobs: list[Job]) -> int:
    """Tell the reaper these jobs are still alive. Returns how many are still ours."""
    if not jobs:
        return 0
    cursor = await conn.execute(
        """
        UPDATE jobs SET heartbeat_at = now()
        FROM unnest(%s::bigint[], %s::text[], %s::int[]) AS lease(id, worker, attempts)
        WHERE jobs.id = lease.id AND jobs.locked_by = lease.worker
          AND jobs.attempts = lease.attempts AND jobs.status = 'running'
        """,
        ([j.id for j in jobs], [j.locked_by for j in jobs], [j.attempts for j in jobs]),
    )
    return cursor.rowcount


async def reap(conn: Conn, stale_after_s: float) -> dict[str, int]:
    """Recover jobs whose worker died (no heartbeat for `stale_after_s`).

    The crashed attempt counts: a job that kills its worker every time ("poison pill") goes
    dead after max_attempts instead of crashing the fleet forever.
    """
    cursor = await conn.execute(
        """
        UPDATE jobs SET
            status = CASE WHEN attempts >= max_attempts THEN 'dead' ELSE 'queued' END,
            finished_at = CASE WHEN attempts >= max_attempts THEN now() END,
            run_at = now(), locked_by = NULL, locked_at = NULL, heartbeat_at = NULL,
            last_error = 'worker stopped heartbeating; reaped'
        WHERE status = 'running' AND heartbeat_at < now() - make_interval(secs => %s)
        RETURNING status
        """,
        (stale_after_s,),
    )
    counts = {"queued": 0, "dead": 0}
    for row in await cursor.fetchall():
        counts[row["status"]] += 1
    return counts
