"""The scan_url job: one mobile page load for one research target (PRD §16).

Same capturer and checks as monitoring. Politeness: the per-domain lock from §12, one target
per domain, and the job was scheduled at least 10 s after that domain's robots.txt fetch.
Only the capture JSON is stored (for re-analysis); screenshots of third-party sites aren't
needed for aggregate findings.
"""

from datetime import UTC, datetime
from uuid import uuid4

from psycopg.types.json import Jsonb

from tagmonitor.browser.capturer import CaptureError
from tagmonitor.browser.ssrf import SsrfError
from tagmonitor.checks.base import SiteConfig
from tagmonitor.checks.registry import run_checks
from tagmonitor.queue.jobs import Job, complete
from tagmonitor.urls import registrable_domain
from tagmonitor.worker.capture_job import domain_lock
from tagmonitor.worker.context import DomainBusy, JobError, WorkerContext


async def run_scan_job(job: Job, ctx: WorkerContext) -> None:
    async with ctx.pool.connection() as conn:
        cursor = await conn.execute(
            "SELECT id, scan_id, url, status FROM scan_targets WHERE id = %s",
            (job.payload["scan_target_id"],),
        )
        target = await cursor.fetchone()
        if target is None or target["status"] != "pending":
            await complete(conn, job)
            return

    started_at = datetime.now(UTC)
    async with domain_lock(ctx.pool, registrable_domain(target["url"])) as acquired:
        if not acquired:
            raise DomainBusy(registrable_domain(target["url"]))
        try:
            result = await ctx.capturer.capture(target["url"], "mobile")
        except CaptureError as exc:
            raise JobError(exc.code, str(exc), transient=exc.transient, device="mobile") from exc
        except SsrfError as exc:
            raise JobError(exc.code, str(exc), transient=False, device="mobile") from exc

    capture = result.capture
    capture_key = f"scans/{target['scan_id']}/{target['id']}-{uuid4().hex[:8]}.json"
    await ctx.storage.put(capture_key, capture.model_dump_json().encode(), "application/json")
    results = run_checks(capture, SiteConfig(url=target["url"]))
    navigation = capture.navigation

    async with ctx.pool.connection() as conn, conn.transaction():
        cursor = await conn.execute(
            """
            INSERT INTO check_runs (scan_id, job_id, device, started_at, finished_at, status,
                                    error_code, error_message, final_url, http_status,
                                    capture_key, duration_ms)
            VALUES (%s, %s, 'mobile', %s, now(), 'completed', %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                target["scan_id"],
                job.id,
                started_at,
                navigation.error_code,
                navigation.error,
                navigation.final_url,
                navigation.status,
                capture_key,
                round(capture.duration_ms),
            ),
        )
        row = await cursor.fetchone()
        assert row is not None
        async with conn.cursor() as cursor:
            await cursor.executemany(
                "INSERT INTO check_results (run_id, check_key, status, code, summary, details) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                [
                    (row["id"], r.check_key, r.status, r.code, r.summary, Jsonb(r.details))
                    for r in results
                ],
            )
        await conn.execute(
            "UPDATE scan_targets SET status = 'done', run_id = %s WHERE id = %s",
            (row["id"], target["id"]),
        )
        await complete(conn, job)


async def record_failed_scan_target(job: Job, ctx: WorkerContext, error: JobError) -> None:
    """A target whose job died is counted as attempted but not reachable."""
    async with ctx.pool.connection() as conn:
        await conn.execute(
            "UPDATE scan_targets SET status = 'failed', skip_reason = %s "
            "WHERE id = %s AND status = 'pending'",
            (error.code, job.payload["scan_target_id"]),
        )
