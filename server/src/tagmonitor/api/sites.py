"""Monitored sites: create, list, read, update, delete, check now (PRD §17).

Every query is scoped by the logged-in user's id. A site that exists but belongs to someone
else gets the same 404 as one that doesn't exist, so ids can't be probed.
"""

from typing import Any
from urllib.parse import SplitResult
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response, status
from psycopg import errors, sql

from tagmonitor.api.deps import PoolDep, ResolverDep, SettingsDep, UserDep
from tagmonitor.api.schemas import (
    ActiveJobOut,
    CheckNowOut,
    CheckStatusOut,
    ExplanationOut,
    LatestResultOut,
    SiteCreate,
    SiteDetailOut,
    SiteOut,
    SiteUpdate,
)
from tagmonitor.browser.ssrf import Resolver, SsrfError, check_user_url
from tagmonitor.checks.explanations import explain
from tagmonitor.checks.registry import ALL_CHECKS, CHECKS_BY_KEY
from tagmonitor.config import Settings
from tagmonitor.queue.jobs import PRIORITY_MANUAL, Conn, enqueue
from tagmonitor.urls import normalize_url, registrable_domain

router = APIRouter(prefix="/api/sites", tags=["sites"])

URL_PROBLEMS = {
    "invalid_url": "That doesn't look like a web address we can check",
    "ssrf_blocked": "That address points to a private network, which we can't monitor",
    "dns_failure": "We couldn't find that domain. Check the spelling",
}

# One query for both the list and a single site: the site row, when it was last checked,
# its latest status per check (from the site_latest_status view), and any queued or running
# job for it. The correlated subqueries each hit an index keyed by site id.
SITES_QUERY = """
SELECT s.id, s.name, s.url, s.check_interval_minutes, s.paused, s.alert_email,
       s.expected_meta_pixel_ids, s.expected_ga4_ids, s.expected_google_ads_ids,
       s.ad_headline, s.ad_primary_text, s.ad_cta, s.created_at, s.next_check_at,
       (SELECT max(r.started_at) FROM check_runs r WHERE r.site_id = s.id) AS last_checked_at,
       (SELECT coalesce(jsonb_object_agg(v.check_key, jsonb_build_object(
                    'status', v.status, 'code', v.code, 'summary', v.summary)), '{}')
        FROM site_latest_status v WHERE v.site_id = s.id) AS statuses,
       (SELECT jsonb_build_object('id', j.id, 'status', j.status)
        FROM jobs j
        WHERE j.dedupe_key = 'site:' || s.id AND j.status IN ('queued', 'running')) AS active_job
FROM sites s
WHERE s.user_id = %(user_id)s AND (%(site_id)s::uuid IS NULL OR s.id = %(site_id)s)
ORDER BY s.created_at
"""


def _site_out(row: dict[str, Any]) -> SiteOut:
    return SiteOut(
        **{k: v for k, v in row.items() if k not in ("statuses", "active_job")},
        statuses={key: CheckStatusOut(**value) for key, value in row["statuses"].items()},
        active_job=ActiveJobOut(**row["active_job"]) if row["active_job"] else None,
    )


async def _fetch_sites(conn: Conn, user_id: UUID, site_id: UUID | None = None) -> list[SiteOut]:
    cursor = await conn.execute(SITES_QUERY, {"user_id": user_id, "site_id": site_id})
    return [_site_out(dict(row)) for row in await cursor.fetchall()]


async def _fetch_site(conn: Conn, user_id: UUID, site_id: UUID) -> SiteOut:
    sites = await _fetch_sites(conn, user_id, site_id)
    if not sites:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Site not found.")
    return sites[0]


async def _checked_url(url: str, settings: Settings, resolver: Resolver) -> SplitResult:
    """The SSRF guard's rules applied when the URL is submitted (it runs again at capture)."""
    try:
        return await check_user_url(url, settings.ssrf_policy(), resolver)
    except SsrfError as exc:
        message = URL_PROBLEMS.get(exc.code, "We can't check that address")
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{message} ({exc}).") from exc


@router.get("")
async def list_sites(user: UserDep, pool: PoolDep) -> list[SiteOut]:
    async with pool.connection() as conn:
        return await _fetch_sites(conn, user.id)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_site(
    body: SiteCreate, user: UserDep, pool: PoolDep, settings: SettingsDep, resolver: ResolverDep
) -> SiteOut:
    url = body.url.strip()
    parts = await _checked_url(url, settings, resolver)
    async with pool.connection() as conn, conn.transaction():
        cursor = await conn.execute(
            """
            INSERT INTO sites (user_id, name, url, normalized_url, registrable_domain,
                               check_interval_minutes, next_check_at, alert_email,
                               expected_meta_pixel_ids, expected_ga4_ids, expected_google_ads_ids,
                               ad_headline, ad_primary_text, ad_cta)
            VALUES (%(user_id)s, %(name)s, %(url)s, %(normalized)s, %(domain)s, %(interval)s,
                    now() + make_interval(mins => %(interval)s), %(alert_email)s, %(meta)s,
                    %(ga4)s, %(ads)s, %(headline)s, %(primary)s, %(cta)s)
            ON CONFLICT (user_id, normalized_url) DO NOTHING
            RETURNING id
            """,
            {
                "user_id": user.id,
                "name": body.name or parts.hostname,
                "url": url,
                "normalized": normalize_url(url),
                "domain": registrable_domain(url),
                "interval": body.check_interval_minutes or 1440,
                "alert_email": body.alert_email or user.email,
                "meta": body.expected_meta_pixel_ids or [],
                "ga4": body.expected_ga4_ids or [],
                "ads": body.expected_google_ads_ids or [],
                "headline": body.ad_headline,
                "primary": body.ad_primary_text,
                "cta": body.ad_cta,
            },
        )
        row = await cursor.fetchone()
        if row is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "You already monitor this page.")
        # The first check runs right away rather than at the next scheduled slot.
        await enqueue(
            conn,
            "capture_and_check",
            {"site_id": str(row["id"]), "reason": "manual"},
            priority=PRIORITY_MANUAL,
            dedupe_key=f"site:{row['id']}",
        )
        return await _fetch_site(conn, user.id, row["id"])


@router.get("/{site_id}")
async def get_site(site_id: UUID, user: UserDep, pool: PoolDep) -> SiteDetailOut:
    async with pool.connection() as conn:
        site = await _fetch_site(conn, user.id, site_id)
        cursor = await conn.execute(
            "SELECT check_key, status, code, summary, details, device, run_id, started_at "
            "FROM site_latest_status WHERE site_id = %s",
            (site_id,),
        )
        rows = {row["check_key"]: row for row in await cursor.fetchall()}
    latest = []
    for check in ALL_CHECKS:  # registry order: Meta, Google..., speed, layout, health
        row = rows.get(check.check_key)
        if row is None:
            continue
        explanation = explain(row["check_key"], row["code"])
        latest.append(
            LatestResultOut(
                check_key=row["check_key"],
                title=CHECKS_BY_KEY[row["check_key"]].title,
                status=row["status"],
                code=row["code"],
                summary=row["summary"],
                details=row["details"],
                explanation=ExplanationOut(**vars(explanation)) if explanation else None,
                device=row["device"],
                run_id=row["run_id"],
                checked_at=row["started_at"],
            )
        )
    return SiteDetailOut(**site.model_dump(), latest_results=latest)


# Columns PATCH may change, and whether null is a meaningful value for them.
UPDATABLE = {
    "name": False,
    "check_interval_minutes": False,
    "alert_email": False,
    "paused": False,
    "expected_meta_pixel_ids": False,
    "expected_ga4_ids": False,
    "expected_google_ads_ids": False,
    "ad_headline": True,
    "ad_primary_text": True,
    "ad_cta": True,
}


@router.patch("/{site_id}")
async def update_site(
    site_id: UUID,
    body: SiteUpdate,
    user: UserDep,
    pool: PoolDep,
    settings: SettingsDep,
    resolver: ResolverDep,
) -> SiteOut:
    provided = body.model_dump(exclude_unset=True)
    values: dict[str, Any] = {
        column: provided[column]
        for column, nullable in UPDATABLE.items()
        if column in provided and (nullable or provided[column] is not None)
    }
    if provided.get("url"):
        url = provided["url"].strip()
        await _checked_url(url, settings, resolver)
        values |= {
            "url": url,
            "normalized_url": normalize_url(url),
            "registrable_domain": registrable_domain(url),
        }

    # Column names come from the UPDATABLE whitelist and are quoted as identifiers; every
    # value is a bound parameter.
    assignments: list[sql.Composable] = [
        sql.SQL("{} = {}").format(sql.Identifier(column), sql.Placeholder(column))
        for column in values
    ]
    if "check_interval_minutes" in values:
        # A shorter interval takes effect now instead of after the old, longer wait.
        assignments.append(
            sql.SQL(
                "next_check_at = least(next_check_at, now() + make_interval(mins => {}))"
            ).format(sql.Placeholder("check_interval_minutes"))
        )
    assignments.append(sql.SQL("updated_at = now()"))
    query = sql.SQL("UPDATE sites SET {} WHERE id = {} AND user_id = {} RETURNING id").format(
        sql.SQL(", ").join(assignments), sql.Placeholder("_id"), sql.Placeholder("_user")
    )

    async with pool.connection() as conn:
        try:
            cursor = await conn.execute(query, values | {"_id": site_id, "_user": user.id})
        except errors.UniqueViolation as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, "You already monitor that page.") from exc
        if await cursor.fetchone() is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Site not found.")
        return await _fetch_site(conn, user.id, site_id)


@router.delete("/{site_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_site(site_id: UUID, user: UserDep, pool: PoolDep) -> None:
    async with pool.connection() as conn:
        cursor = await conn.execute(
            "DELETE FROM sites WHERE id = %s AND user_id = %s RETURNING id", (site_id, user.id)
        )
        if await cursor.fetchone() is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Site not found.")


@router.post("/{site_id}/check-now")
async def check_now(
    site_id: UUID, user: UserDep, pool: PoolDep, settings: SettingsDep, response: Response
) -> CheckNowOut:
    """Run a check as soon as possible. If one is already queued it's moved to the front; if
    one is running, the caller just follows that one. Otherwise at most once per 5 minutes."""
    async with pool.connection() as conn, conn.transaction():
        cursor = await conn.execute(
            "SELECT last_check_requested_at, "
            "extract(epoch FROM now() - last_check_requested_at) AS seconds_since "
            "FROM sites WHERE id = %s AND user_id = %s FOR UPDATE",
            (site_id, user.id),
        )
        site = await cursor.fetchone()
        if site is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Site not found.")

        cursor = await conn.execute(
            "UPDATE jobs SET priority = greatest(priority, %s), run_at = least(run_at, now()) "
            "WHERE dedupe_key = %s AND status IN ('queued', 'running') "
            "RETURNING id",
            (PRIORITY_MANUAL, f"site:{site_id}"),
        )
        active = await cursor.fetchone()
        if active is not None:
            response.status_code = status.HTTP_200_OK
            return CheckNowOut(job_id=active["id"])

        cooldown = settings.check_now_cooldown_seconds
        if site["seconds_since"] is not None and site["seconds_since"] < cooldown:
            wait = int(cooldown - site["seconds_since"]) + 1
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                f"This site was checked moments ago. Try again in {wait} seconds.",
                headers={"Retry-After": str(wait)},
            )
        job_id = await enqueue(
            conn,
            "capture_and_check",
            {"site_id": str(site_id), "reason": "manual"},
            priority=PRIORITY_MANUAL,
            dedupe_key=f"site:{site_id}",
        )
        assert job_id is not None  # we hold the site row lock and saw no active job
        await conn.execute(
            "UPDATE sites SET last_check_requested_at = now() WHERE id = %s", (site_id,)
        )
    response.status_code = status.HTTP_202_ACCEPTED
    return CheckNowOut(job_id=job_id)
