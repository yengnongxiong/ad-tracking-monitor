"""The retention job: delete monitoring history older than RETENTION_DAYS (PRD §11).

Once a day the scheduler enqueues one `retention` job (scheduler.schedule_retention). It
deletes, in batches:

- check runs (and, by cascade, their results) older than the cutoff, plus their capture JSON
  and screenshots in object storage. Each site's latest run per device is always kept, so a
  site paused for months still shows its last known state.
- finished jobs older than the cutoff, expired sessions, login attempts older than a day
  (the rate limit looks back 15 minutes), and model answers cached before the cutoff.

Kept on purpose: alert history (small, and it's the user's record of what happened), LLM usage
(cost history), and research-scan runs (a scan is re-analyzable until it's deleted).

Rows are deleted before their objects: if deleting an object fails, the result is an orphaned
file (harmless, cleaned up by bucket lifecycle rules if needed), never a run pointing at a
missing screenshot. Each batch commits on its own, so a huge backlog never holds one giant
transaction, and a crash just leaves the rest for tomorrow.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from tagmonitor.db.pool import Pool
from tagmonitor.queue.jobs import Job, complete
from tagmonitor.storage import ObjectStorage
from tagmonitor.worker.context import WorkerContext

log = logging.getLogger(__name__)

BATCH_SIZE = 500  # runs per batch: at most 1,000 objects, S3's DeleteObjects limit


@dataclass
class RetentionReport:
    cutoff: datetime
    runs: int = 0
    objects: int = 0
    other: dict[str, int] = field(default_factory=dict)


async def run_retention(job: Job, ctx: WorkerContext) -> None:
    report = await purge(ctx.pool, ctx.storage, days=ctx.settings.retention_days)
    log.info(
        "retention: deleted %d runs and %d stored objects before %s; %s",
        report.runs,
        report.objects,
        report.cutoff.isoformat(),
        report.other,
    )
    async with ctx.pool.connection() as conn:
        await complete(conn, job)


async def purge(
    pool: Pool, storage: ObjectStorage, *, days: int, batch_size: int = BATCH_SIZE
) -> RetentionReport:
    async with pool.connection() as conn:
        cursor = await conn.execute("SELECT now() - %s AS cutoff", (timedelta(days=days),))
        row = await cursor.fetchone()
    assert row is not None
    report = RetentionReport(cutoff=row["cutoff"])

    while True:
        async with pool.connection() as conn:
            cursor = await conn.execute(
                """
                DELETE FROM check_runs
                WHERE id IN (
                    SELECT r.id FROM check_runs r
                    WHERE r.site_id IS NOT NULL
                      AND r.started_at < %(cutoff)s
                      -- not the site's latest run on this device: a newer one exists
                      -- (EXISTS stops at the first, found via check_runs_site_history_idx)
                      AND EXISTS (SELECT 1 FROM check_runs newer
                                  WHERE newer.site_id = r.site_id
                                    AND newer.device = r.device
                                    AND newer.id > r.id)
                    ORDER BY r.started_at
                    LIMIT %(batch)s
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING capture_key, screenshot_key
                """,
                {"cutoff": report.cutoff, "batch": batch_size},
            )
            deleted = await cursor.fetchall()
        if not deleted:
            break
        keys = [
            key
            for row in deleted
            for key in (row["capture_key"], row["screenshot_key"])
            if key is not None
        ]
        await storage.delete(keys)
        report.runs += len(deleted)
        report.objects += len(keys)

    report.other["jobs"] = 0
    while True:  # finished jobs pile up fastest, so they go in batches too
        async with pool.connection() as conn:
            cursor = await conn.execute(
                "DELETE FROM jobs WHERE id IN (SELECT id FROM jobs "
                "WHERE status IN ('succeeded', 'dead') AND finished_at < %s LIMIT %s)",
                (report.cutoff, batch_size * 10),
            )
        if cursor.rowcount <= 0:
            break
        report.other["jobs"] += cursor.rowcount

    other = {
        "sessions": "DELETE FROM sessions WHERE expires_at < now()",
        "login_attempts": (
            "DELETE FROM login_attempts WHERE attempted_at < now() - interval '1 day'"
        ),
        "llm_cache": "DELETE FROM llm_cache WHERE created_at < %(cutoff)s",
    }
    async with pool.connection() as conn:
        for name, statement in other.items():
            cursor = await conn.execute(statement, {"cutoff": report.cutoff})
            report.other[name] = cursor.rowcount
    return report
