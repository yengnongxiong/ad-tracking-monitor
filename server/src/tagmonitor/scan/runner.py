"""Start a research scan: vet targets, check robots.txt, enqueue one job per domain.

The page loads themselves are ordinary scan_url jobs, done by the regular workers at the
lowest priority, so monitoring always goes first.
"""

import asyncio
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from tagmonitor.browser.ssrf import Resolver, SsrfPolicy
from tagmonitor.queue.jobs import PRIORITY_SCAN, Conn, enqueue
from tagmonitor.scan.robots import ROBOTS_USER_AGENT, RobotsVerdict, check_robots
from tagmonitor.scan.targets import Target

# PRD §16: at least 10 s between requests to the same domain. The robots.txt fetch is one,
# so the page load is scheduled no earlier than this after it.
SAME_DOMAIN_GAP_S = 10
ROBOTS_CONCURRENCY = 10


@dataclass
class ScanPlan:
    scan_id: str
    counts: Counter[str]  # enqueued / skipped:<reason> / failed:<reason>


async def start_scan(
    conn: Conn,
    name: str,
    targets: list[Target],
    policy: SsrfPolicy,
    resolver: Resolver,
    config: dict[str, Any],
) -> ScanPlan:
    """Check robots.txt for every target first (network only, nothing written), then record
    the scan, its targets and their jobs in one transaction: all or nothing."""
    semaphore = asyncio.Semaphore(ROBOTS_CONCURRENCY)

    async def verdict(target: Target) -> RobotsVerdict | None:
        if target.skip_reason:
            return None
        async with semaphore:
            return await check_robots(target.url, policy, resolver)

    verdicts = await asyncio.gather(*(verdict(t) for t in targets))

    counts: Counter[str] = Counter()
    async with conn.transaction():
        cursor = await conn.execute(
            "INSERT INTO scans (name, config) VALUES (%s, %s) RETURNING id",
            (name, Jsonb(config | {"robots_user_agent": ROBOTS_USER_AGENT})),
        )
        row = await cursor.fetchone()
        assert row is not None
        scan_id = str(row["id"])
        for target, robots in zip(targets, verdicts, strict=True):
            status, reason = _target_status(target, robots)
            cursor = await conn.execute(
                "INSERT INTO scan_targets (scan_id, url, category, source, status, skip_reason) "
                "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (scan_id, url) DO NOTHING "
                "RETURNING id",
                (scan_id, target.url, target.category, target.source, status, reason),
            )
            inserted = await cursor.fetchone()
            if inserted is None:
                counts["skipped:duplicate_url"] += 1
            elif status == "pending":
                await enqueue(
                    conn,
                    "scan_url",
                    {"scan_target_id": inserted["id"]},
                    priority=PRIORITY_SCAN,
                    dedupe_key=f"scan_target:{inserted['id']}",
                    delay_s=SAME_DOMAIN_GAP_S,
                )
                counts["enqueued"] += 1
            else:
                counts[f"{status}:{reason}"] += 1
    return ScanPlan(scan_id=scan_id, counts=counts)


def _target_status(target: Target, robots: RobotsVerdict | None) -> tuple[str, str | None]:
    if target.skip_reason:
        return "skipped", target.skip_reason
    if robots is None or robots.outcome == "allowed":
        return "pending", None
    if robots.outcome == "unreachable":
        return "failed", "unreachable"  # counted as attempted but not reachable
    return "skipped", "robots_disallowed" if robots.outcome == "disallowed" else "robots_error"


async def scan_progress(conn: Conn, scan_id: str) -> Counter[str]:
    cursor = await conn.execute(
        "SELECT status, count(*) AS n FROM scan_targets WHERE scan_id = %s GROUP BY status",
        (scan_id,),
    )
    return Counter({row["status"]: row["n"] for row in await cursor.fetchall()})


def load_scan_config(targets_file: Path, throttling: str) -> dict[str, Any]:
    return {"targets_file": targets_file.name, "device": "mobile", "throttling": throttling}
