"""The scheduler: turns sites that are due into capture jobs (PRD §12).

It runs as a loop inside every worker, but a transaction-scoped advisory lock lets only one
of them do a tick at a time (ADR-011). Even without the lock the work would be correct (the
due-sites query uses SKIP LOCKED and the insert is deduplicated), so the lock just avoids
wasted effort.
"""

from tagmonitor.queue.jobs import PRIORITY_SCHEDULED, Conn

# Arbitrary constant naming "the scheduler lock" among advisory locks. Two-int keys live in a
# different lock space from the one-bigint keys used for per-domain politeness.
SCHEDULER_LOCK = (7311, 1)
BATCH_SIZE = 100
INTERVAL_JITTER = 0.05


async def schedule_due_sites(conn: Conn) -> int:
    """Enqueue capture jobs for due sites; returns how many were enqueued.

    One transaction: pick due sites (skipping rows another tick has locked), insert one job
    per site unless one is already queued or running, and push next_check_at out by the
    interval ±5% jitter so a thousand sites added at once don't stay in lockstep forever.
    """
    async with conn.transaction():
        cursor = await conn.execute(
            "SELECT pg_try_advisory_xact_lock(%s, %s) AS locked", SCHEDULER_LOCK
        )
        row = await cursor.fetchone()
        if row is None or not row["locked"]:
            return 0  # another worker's scheduler is ticking right now

        cursor = await conn.execute(
            """
            SELECT id FROM sites
            WHERE NOT paused AND next_check_at <= now()
            ORDER BY next_check_at
            LIMIT %s
            FOR UPDATE SKIP LOCKED
            """,
            (BATCH_SIZE,),
        )
        site_ids = [r["id"] for r in await cursor.fetchall()]
        if not site_ids:
            return 0

        cursor = await conn.execute(
            """
            INSERT INTO jobs (type, payload, priority, dedupe_key)
            SELECT 'capture_and_check',
                   jsonb_build_object('site_id', site_id, 'reason', 'scheduled'),
                   %s,
                   'site:' || site_id
            FROM unnest(%s::uuid[]) AS due(site_id)
            ON CONFLICT (dedupe_key) WHERE status IN ('queued', 'running') DO NOTHING
            """,
            (PRIORITY_SCHEDULED, site_ids),
        )
        enqueued = cursor.rowcount

        await conn.execute(
            """
            UPDATE sites
            SET next_check_at = now() + make_interval(mins => check_interval_minutes)
                                * (1 - %(jitter)s + random() * 2 * %(jitter)s)
            WHERE id = ANY(%(ids)s)
            """,
            {"jitter": INTERVAL_JITTER, "ids": site_ids},
        )
        return enqueued
