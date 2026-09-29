"""The capture_and_check job: load a site on mobile and desktop, store the evidence, save results.

Flow: load the site -> take the per-domain politeness lock -> capture each device -> upload
screenshot + capture JSON -> release the lock -> ask the model about message match (sites with
ad copy only) -> run the checks -> in ONE transaction, save runs and results and mark the job
done. If anything before the commit fails, nothing is saved and the job retries.
"""

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

from psycopg.types.json import Jsonb

from tagmonitor.alerts.outbox import apply_results, worst_results
from tagmonitor.browser.capturer import CaptureError
from tagmonitor.browser.ssrf import SsrfError
from tagmonitor.checks.base import CheckResult
from tagmonitor.checks.message_match import MessageMatchOutcome, PageText
from tagmonitor.checks.registry import run_checks
from tagmonitor.db.pool import Pool
from tagmonitor.llm.message_match import MessageMatcher
from tagmonitor.llm.prompts import PromptError
from tagmonitor.page_capture import Device, PageCapture
from tagmonitor.queue.jobs import Conn, Job, complete
from tagmonitor.sites import Site, get_site
from tagmonitor.worker.context import DomainBusy, JobError, WorkerContext

log = logging.getLogger(__name__)

DEVICES: tuple[Device, ...] = ("mobile", "desktop")


@dataclass
class DeviceRun:
    device: Device
    started_at: datetime
    duration_ms: int
    capture: PageCapture
    capture_key: str
    screenshot_key: str | None
    results: list[CheckResult] = field(default_factory=list)


@asynccontextmanager
async def domain_lock(pool: Pool, domain: str) -> AsyncIterator[bool]:
    """Per-domain politeness (PRD §12): at most one page load per registrable domain at a time,
    across all workers. A session advisory lock lives exactly as long as the connection holding
    it, so a crashed worker can never leave a domain locked."""
    async with pool.connection() as conn:
        cursor = await conn.execute(
            "SELECT pg_try_advisory_lock(hashtextextended(%s, 0)) AS locked", (domain,)
        )
        row = await cursor.fetchone()
        locked = bool(row and row["locked"])
        try:
            yield locked
        finally:
            if locked:
                await conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (domain,))


async def run_capture_job(job: Job, ctx: WorkerContext) -> None:
    async with ctx.pool.connection() as conn:
        site = await get_site(conn, job.payload["site_id"])
        if site is None or (site.paused and job.payload.get("reason") == "scheduled"):
            await complete(conn, job)  # deleted or paused since it was queued: nothing to do
            return

    async with domain_lock(ctx.pool, site.registrable_domain) as acquired:
        if not acquired:
            raise DomainBusy(site.registrable_domain)
        runs = [await capture_device(ctx, job, site, device) for device in DEVICES]

    message_match = await assess_message_match(ctx, site, runs)
    for run in runs:
        run.results = run_checks(run.capture, site.check_config(), message_match)

    async with ctx.pool.connection() as conn, conn.transaction():
        for run in runs:
            await save_run(conn, job, site, run)
        # Completing inside the same transaction: if another worker has taken the job over
        # (LostLease), the results above roll back instead of being saved twice.
        await complete(conn, job)
        # Alert states, alerts and their delivery jobs commit together with the results
        # (transactional outbox, ADR-013).
        observed = worst_results([run.results for run in runs])
        await apply_results(conn, site, observed, datetime.now(UTC), ctx.settings)


async def capture_device(ctx: WorkerContext, job: Job, site: Site, device: Device) -> DeviceRun:
    started_at = datetime.now(UTC)
    clock = time.monotonic()
    try:
        result = await ctx.capturer.capture(site.url, device)
    except CaptureError as exc:
        raise JobError(exc.code, str(exc), transient=exc.transient, device=device) from exc
    except SsrfError as exc:
        raise JobError(exc.code, str(exc), transient=False, device=device) from exc
    capture = result.capture
    if capture.navigation.error_code == "ssrf_blocked":
        # Permanent per PRD §12: retrying won't make a private address public.
        raise JobError(
            "ssrf_blocked", capture.navigation.error or "", transient=False, device=device
        )

    prefix = f"sites/{site.id}/{started_at:%Y/%m/%d}/{job.id}-{device}-{uuid4().hex[:8]}"
    screenshot_key = None
    if result.screenshot_jpeg is not None:
        screenshot_key = f"{prefix}.jpg"
        await ctx.storage.put(screenshot_key, result.screenshot_jpeg, "image/jpeg")
    capture.screenshot_key = screenshot_key
    capture_key = f"{prefix}.json"
    await ctx.storage.put(capture_key, capture.model_dump_json().encode(), "application/json")

    return DeviceRun(
        device=device,
        started_at=started_at,
        duration_ms=round((time.monotonic() - clock) * 1000),
        capture=capture,
        capture_key=capture_key,
        screenshot_key=screenshot_key,
    )


async def assess_message_match(
    ctx: WorkerContext, site: Site, runs: list[DeviceRun]
) -> MessageMatchOutcome | None:
    """The model's verdict on the mobile page, or None when the site has no ad copy (then
    the message match check doesn't run). A failed model call is a result, not a job failure:
    it must never cost the site its other checks."""
    ad = site.ad_copy()
    if ad is None:
        return None
    mobile = next(run for run in runs if run.device == "mobile")
    page = PageText.from_capture(mobile.capture)
    if page is None:
        return MessageMatchOutcome()  # the page didn't load; the check says so
    try:
        matcher = MessageMatcher.from_settings(ctx.pool, ctx.llm, ctx.settings)
    except PromptError as exc:
        log.exception("message match prompt misconfigured")
        return MessageMatchOutcome(error="llm_error", error_detail=str(exc))
    return await matcher.assess(ad, page)


async def save_run(conn: Conn, job: Job, site: Site, run: DeviceRun) -> int:
    navigation = run.capture.navigation
    cursor = await conn.execute(
        """
        INSERT INTO check_runs (site_id, job_id, device, started_at, finished_at, status,
                                error_code, error_message, final_url, http_status, capture_key,
                                screenshot_key, duration_ms)
        VALUES (%s, %s, %s, %s, now(), 'completed', %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            site.id,
            job.id,
            run.device,
            run.started_at,
            navigation.error_code,  # a website failure is still a completed run (ADR-006)
            navigation.error,
            navigation.final_url,
            navigation.status,
            run.capture_key,
            run.screenshot_key,
            run.duration_ms,
        ),
    )
    row = await cursor.fetchone()
    assert row is not None
    run_id = int(row["id"])
    async with conn.cursor() as cursor:
        await cursor.executemany(
            "INSERT INTO check_results (run_id, check_key, status, code, summary, details) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            [
                (run_id, r.check_key, r.status, r.code, r.summary, Jsonb(r.details))
                for r in run.results
            ],
        )
    return run_id


async def record_failed_capture(job: Job, ctx: WorkerContext, error: JobError) -> None:
    """When a capture job dies, leave an error run so the site's history shows why."""
    async with ctx.pool.connection() as conn:
        if await get_site(conn, job.payload["site_id"]) is None:
            return
        await conn.execute(
            """
            INSERT INTO check_runs (site_id, job_id, device, started_at, finished_at, status,
                                    error_code, error_message)
            VALUES (%s, %s, %s, now(), now(), 'error', %s, %s)
            """,
            (job.payload["site_id"], job.id, error.device or "mobile", error.code, str(error)),
        )
