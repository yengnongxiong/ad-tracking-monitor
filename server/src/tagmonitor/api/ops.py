"""Queue health for operators (PRD §17): GET /api/ops/queue, admins only."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from tagmonitor.api.deps import AdminDep, PoolDep

router = APIRouter(prefix="/api/ops", tags=["ops"])


class DeadJob(BaseModel):
    id: int
    type: str
    last_error: str | None
    finished_at: datetime | None


class QueueStats(BaseModel):
    depth: list[dict[str, Any]]  # [{type, status, count}] for active jobs
    oldest_queued_age_s: float | None  # how late the most overdue due job is
    last_24h: dict[str, Any]  # succeeded, dead, success_rate, p50_s, p95_s
    dead_jobs: list[DeadJob]


@router.get("/queue")
async def queue_stats(_: AdminDep, pool: PoolDep) -> QueueStats:
    async with pool.connection() as conn:
        depth = await (
            await conn.execute(
                "SELECT type, status, count(*) AS count FROM jobs "
                "WHERE status IN ('queued', 'running') GROUP BY type, status ORDER BY type, status"
            )
        ).fetchall()
        oldest = await (
            await conn.execute(
                "SELECT extract(epoch FROM now() - min(run_at)) AS age FROM jobs "
                "WHERE status = 'queued' AND run_at <= now()"
            )
        ).fetchone()
        # Duration = claim to finish of the successful attempt. Uses jobs_finished_at_idx.
        window = await (
            await conn.execute(
                """
                SELECT count(*) FILTER (WHERE status = 'succeeded') AS succeeded,
                       count(*) FILTER (WHERE status = 'dead') AS dead,
                       percentile_cont(0.5) WITHIN GROUP (
                           ORDER BY extract(epoch FROM finished_at - locked_at))
                           FILTER (WHERE status = 'succeeded') AS p50_s,
                       percentile_cont(0.95) WITHIN GROUP (
                           ORDER BY extract(epoch FROM finished_at - locked_at))
                           FILTER (WHERE status = 'succeeded') AS p95_s
                FROM jobs
                WHERE finished_at > now() - interval '24 hours'
                """
            )
        ).fetchone()
        dead = await (
            await conn.execute(
                "SELECT id, type, last_error, finished_at FROM jobs WHERE status = 'dead' "
                "ORDER BY finished_at DESC NULLS LAST LIMIT 20"
            )
        ).fetchall()

    assert window is not None
    finished = window["succeeded"] + window["dead"]
    return QueueStats(
        depth=[dict(row) for row in depth],
        oldest_queued_age_s=oldest["age"] if oldest else None,
        last_24h={
            "succeeded": window["succeeded"],
            "dead": window["dead"],
            "success_rate": window["succeeded"] / finished if finished else None,
            "p50_s": window["p50_s"],
            "p95_s": window["p95_s"],
        },
        dead_jobs=[DeadJob(**row) for row in dead],
    )
