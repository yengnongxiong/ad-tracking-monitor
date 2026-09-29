"""Run history, run details, job polling and trends (PRD §17). All scoped to the user."""

import base64
import binascii
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from tagmonitor.api.deps import PoolDep, StorageDep, UserDep
from tagmonitor.api.schemas import (
    ExplanationOut,
    JobOut,
    ResultOut,
    RunDetailOut,
    RunResultBrief,
    RunsPage,
    RunSummaryOut,
    TrendOut,
    TrendPoint,
)
from tagmonitor.checks.explanations import explain
from tagmonitor.checks.registry import CHECKS_BY_KEY
from tagmonitor.queue.jobs import Conn

router = APIRouter(prefix="/api", tags=["runs"])

SCREENSHOT_LINK_SECONDS = 600  # presigned links expire after 10 minutes


async def _require_site(conn: Conn, site_id: UUID, user_id: UUID) -> None:
    cursor = await conn.execute(
        "SELECT 1 FROM sites WHERE id = %s AND user_id = %s", (site_id, user_id)
    )
    if await cursor.fetchone() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Site not found.")


def _encode_cursor(started_at: datetime, run_id: int) -> str:
    return base64.urlsafe_b64encode(f"{started_at.isoformat()}|{run_id}".encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, int]:
    try:
        started_at, run_id = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
        return datetime.fromisoformat(started_at), int(run_id)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid cursor.") from exc


@router.get("/sites/{site_id}/runs")
async def list_runs(
    site_id: UUID,
    user: UserDep,
    pool: PoolDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: str | None = None,
) -> RunsPage:
    """Newest first, with keyset pagination on (started_at, id): each page continues strictly
    after the last row of the previous one, so pages stay stable while new runs arrive and
    deep pages cost the same as the first (no OFFSET scan)."""
    before_at, before_id = _decode_cursor(cursor) if cursor else (None, None)
    async with pool.connection() as conn:
        await _require_site(conn, site_id, user.id)
        rows = await (
            await conn.execute(
                """
                SELECT id, device, started_at, status, error_code, http_status, final_url
                FROM check_runs
                WHERE site_id = %(site_id)s
                  AND (%(before_at)s::timestamptz IS NULL
                       OR (started_at, id) < (%(before_at)s, %(before_id)s))
                ORDER BY started_at DESC, id DESC
                LIMIT %(limit)s
                """,
                {
                    "site_id": site_id,
                    "before_at": before_at,
                    "before_id": before_id,
                    "limit": limit + 1,  # one extra row tells us whether another page exists
                },
            )
        ).fetchall()
        page = rows[:limit]
        results = await (
            await conn.execute(
                "SELECT run_id, check_key, status, code FROM check_results WHERE run_id = ANY(%s)",
                ([row["id"] for row in page],),
            )
        ).fetchall()

    by_run: dict[int, list[RunResultBrief]] = {}
    for r in results:
        by_run.setdefault(r["run_id"], []).append(
            RunResultBrief(check_key=r["check_key"], status=r["status"], code=r["code"])
        )
    runs = [RunSummaryOut(**row, results=by_run.get(row["id"], [])) for row in page]
    next_cursor = (
        _encode_cursor(page[-1]["started_at"], page[-1]["id"]) if len(rows) > limit else None
    )
    return RunsPage(runs=runs, next_cursor=next_cursor)


@router.get("/runs/{run_id}")
async def get_run(run_id: int, user: UserDep, pool: PoolDep, storage: StorageDep) -> RunDetailOut:
    async with pool.connection() as conn:
        run = await (
            await conn.execute(
                """
                SELECT r.id, r.site_id, r.device, r.started_at, r.status, r.error_code,
                       r.http_status, r.final_url, r.duration_ms, r.screenshot_key, r.capture_key
                FROM check_runs r JOIN sites s ON s.id = r.site_id
                WHERE r.id = %s AND s.user_id = %s
                """,
                (run_id, user.id),
            )
        ).fetchone()
        if run is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found.")
        rows = await (
            await conn.execute(
                "SELECT check_key, status, code, summary, details FROM check_results "
                "WHERE run_id = %s ORDER BY id",
                (run_id,),
            )
        ).fetchall()

    results = []
    for row in rows:
        explanation = explain(row["check_key"], row["code"])
        check = CHECKS_BY_KEY.get(row["check_key"])
        results.append(
            ResultOut(
                **row,
                title=check.title if check else row["check_key"],
                explanation=ExplanationOut(**vars(explanation)) if explanation else None,
            )
        )

    def link(key: str | None) -> str | None:
        return storage.presigned_url(key, SCREENSHOT_LINK_SECONDS) if key else None

    return RunDetailOut(
        id=run["id"],
        site_id=run["site_id"],
        device=run["device"],
        started_at=run["started_at"],
        status=run["status"],
        error_code=run["error_code"],
        http_status=run["http_status"],
        final_url=run["final_url"],
        duration_ms=run["duration_ms"],
        screenshot_url=link(run["screenshot_key"]),
        capture_url=link(run["capture_key"]),
        results=results,
    )


@router.get("/jobs/{job_id}")
async def get_job(job_id: int, user: UserDep, pool: PoolDep) -> JobOut:
    """For the dashboard to poll while a check runs. Only jobs about the user's own sites."""
    async with pool.connection() as conn:
        row = await (
            await conn.execute(
                """
                SELECT j.id, j.type, j.status, j.payload->>'reason' AS reason, j.attempts,
                       j.last_error, j.created_at, j.finished_at
                FROM jobs j JOIN sites s ON s.id::text = j.payload->>'site_id'
                WHERE j.id = %s AND s.user_id = %s
                """,
                (job_id, user.id),
            )
        ).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found.")
    return JobOut(**row)


@router.get("/sites/{site_id}/trends")
async def trends(
    site_id: UUID,
    user: UserDep,
    pool: PoolDep,
    metric: Literal["lcp"] = "lcp",
    days: Annotated[int, Query(ge=1, le=90)] = 30,
) -> TrendOut:
    """Mobile LCP over time (lab measurements from the speed check)."""
    async with pool.connection() as conn:
        await _require_site(conn, site_id, user.id)
        rows = await (
            await conn.execute(
                """
                SELECT r.started_at AS at, (cr.details->>'lcp_ms')::float AS value
                FROM check_runs r
                JOIN check_results cr ON cr.run_id = r.id AND cr.check_key = 'page_speed'
                WHERE r.site_id = %s AND r.started_at >= now() - make_interval(days => %s)
                  AND cr.details->>'lcp_ms' IS NOT NULL
                ORDER BY r.started_at
                """,
                (site_id, days),
            )
        ).fetchall()
    return TrendOut(metric=metric, unit="ms", points=[TrendPoint(**row) for row in rows])
