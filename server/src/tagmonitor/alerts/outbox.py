"""Apply a capture job's results to the alert state machines (PRD §13, ADR-013).

Runs inside the same transaction that saves the results. Alerts are written as rows (with the
email already rendered) plus a send_alert job: a *transactional outbox*. Either the results,
the new states, the alerts and their delivery jobs are all committed, or none are, so an alert
is never lost (committed with no job to send it) and never sent for results that rolled back.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from tagmonitor.alerts.content import AlertFacts, render_alert
from tagmonitor.alerts.state_machine import CheckState, Transition, transition
from tagmonitor.checks.base import CheckResult, worst_status
from tagmonitor.checks.registry import CHECKS_BY_KEY
from tagmonitor.config import Settings
from tagmonitor.queue.jobs import PRIORITY_CONFIRM, PRIORITY_SEND_ALERT, Conn, enqueue
from tagmonitor.sites import Site


def worst_results(results_per_device: list[list[CheckResult]]) -> dict[str, CheckResult]:
    """One result per check: the worst across devices (the first device wins ties), so a pixel
    broken only on mobile still counts as broken."""
    chosen: dict[str, CheckResult] = {}
    for results in results_per_device:
        for result in results:
            current = chosen.get(result.check_key)
            if current is None or worst_status([current.status, result.status]) != current.status:
                chosen[result.check_key] = result
    return chosen


@dataclass(frozen=True)
class FailureHistory:
    last_worked_at: datetime | None
    failing_since: datetime | None  # start of the most recent failing streak


async def failure_history(conn: Conn, site_id: object, check_key: str) -> FailureHistory:
    """When did this check last work, and when did the current failing streak start?

    One row per job (a job's devices collapsed: failed if any device failed), then lag() finds
    streak starts: a failed check whose previous check didn't fail. "error" results (couldn't
    evaluate) are left out so a page outage doesn't split or end a tag's failing streak.
    """
    cursor = await conn.execute(
        """
        WITH per_job AS (
            SELECT min(r.started_at) AS checked_at,
                   bool_or(cr.status = 'fail') AS failed
            FROM check_results cr
            JOIN check_runs r ON r.id = cr.run_id
            WHERE r.site_id = %(site_id)s AND cr.check_key = %(check_key)s
              AND cr.status <> 'error'
            GROUP BY r.job_id
        ),
        marked AS (
            SELECT checked_at, failed,
                   lag(failed) OVER (ORDER BY checked_at) AS previous_failed
            FROM per_job
        )
        SELECT max(checked_at) FILTER (WHERE NOT failed) AS last_worked_at,
               max(checked_at) FILTER (WHERE failed AND previous_failed IS NOT TRUE)
                   AS failing_since
        FROM marked
        """,
        {"site_id": site_id, "check_key": check_key},
    )
    row = await cursor.fetchone()
    assert row is not None  # an aggregate always returns one row
    return FailureHistory(row["last_worked_at"], row["failing_since"])


async def apply_results(
    conn: Conn, site: Site, observed: dict[str, CheckResult], now: datetime, settings: Settings
) -> None:
    cursor = await conn.execute(
        "SELECT * FROM site_check_states WHERE site_id = %s FOR UPDATE", (site.id,)
    )
    states = {
        row["check_key"]: CheckState(
            state=row["state"],
            consecutive_fails=row["consecutive_fails"],
            state_entered_at=row["state_entered_at"],
            last_alerted_at=row["last_alerted_at"],
            last_pass_at=row["last_pass_at"],
        )
        for row in await cursor.fetchall()
    }

    needs_confirmation = False
    for check_key, result in observed.items():
        previous = states.get(check_key)
        change = transition(previous, result.status, now)
        if change is None:
            continue
        await _save_state(conn, site, check_key, change.new_state)
        needs_confirmation |= change.enqueue_confirm
        if change.alert is not None:
            await _create_alert(conn, site, result, previous, change, now, settings)

    if needs_confirmation:
        # One confirmation re-check per site, however many checks just started failing. The
        # finished job has already been marked succeeded in this transaction, so the site's
        # dedupe key is free.
        await enqueue(
            conn,
            "capture_and_check",
            {"site_id": str(site.id), "reason": "confirm"},
            priority=PRIORITY_CONFIRM,
            dedupe_key=f"site:{site.id}",
            delay_s=settings.confirm_delay_seconds,
        )


async def _save_state(conn: Conn, site: Site, check_key: str, state: CheckState) -> None:
    await conn.execute(
        """
        INSERT INTO site_check_states (site_id, check_key, state, consecutive_fails,
                                       state_entered_at, last_alerted_at, last_pass_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (site_id, check_key) DO UPDATE SET
            state = EXCLUDED.state,
            consecutive_fails = EXCLUDED.consecutive_fails,
            state_entered_at = EXCLUDED.state_entered_at,
            last_alerted_at = EXCLUDED.last_alerted_at,
            last_pass_at = EXCLUDED.last_pass_at
        """,
        (
            site.id,
            check_key,
            state.state,
            state.consecutive_fails,
            state.state_entered_at,
            state.last_alerted_at,
            state.last_pass_at,
        ),
    )


def alert_dedupe_key(
    site: Site, check_key: str, previous: CheckState | None, change: Transition
) -> str:
    """Same incident, same key: re-applying a transition (a retried job) can't double-alert.

    Failure and recovery alerts are keyed by when their state began. Every reminder in one
    incident shares that time, so a reminder is keyed by the alert it follows instead.
    """
    if change.alert == "reminder":
        anchor = previous.last_alerted_at if previous else None
    else:
        anchor = change.new_state.state_entered_at
    stamp = anchor.isoformat() if anchor else "none"
    return f"{site.id}:{check_key}:{change.alert}:{stamp}"


async def _create_alert(
    conn: Conn,
    site: Site,
    result: CheckResult,
    previous: CheckState | None,
    change: Transition,
    now: datetime,
    settings: Settings,
) -> None:
    assert change.alert is not None
    history = await failure_history(conn, site.id, result.check_key)
    email = render_alert(
        AlertFacts(
            kind=change.alert,
            site_name=site.name,
            site_url=site.url,
            check_key=result.check_key,
            check_title=CHECKS_BY_KEY[result.check_key].title,
            code=result.code,
            summary=result.summary,
            checked_at=now,
            last_worked_at=history.last_worked_at,
            failing_since=history.failing_since,
            dashboard_url=f"{settings.app_base_url}/sites/{site.id}",
            confirm_delay=timedelta(seconds=settings.confirm_delay_seconds),
        )
    )
    cursor = await conn.execute(
        """
        INSERT INTO alerts (site_id, check_key, kind, dedupe_key, subject, body_text, body_html)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (dedupe_key) DO NOTHING
        RETURNING id
        """,
        (
            site.id,
            result.check_key,
            change.alert,
            alert_dedupe_key(site, result.check_key, previous, change),
            email.subject,
            email.text,
            email.html,
        ),
    )
    row = await cursor.fetchone()
    if row is not None:
        await enqueue(
            conn,
            "send_alert",
            {"alert_id": row["id"]},
            priority=PRIORITY_SEND_ALERT,
            dedupe_key=f"alert:{row['id']}",
        )
