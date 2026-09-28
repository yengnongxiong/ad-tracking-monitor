"""Alerting against the real database: PRD §13's scenarios, the outbox, and the history query."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from tagmonitor.alerts.outbox import (
    alert_dedupe_key,
    apply_results,
    failure_history,
    worst_results,
)
from tagmonitor.alerts.state_machine import CheckState, transition
from tagmonitor.checks.base import CheckResult, Status
from tagmonitor.config import Settings
from tagmonitor.db.pool import Pool
from tagmonitor.sites import Site, get_site
from tests.factories import insert_job, insert_site, insert_user

T0 = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
SETTINGS = Settings(confirm_delay_seconds=600, app_base_url="https://app.example")

CODES = {"pass": "firing", "fail": "installed_not_firing", "error": "not_evaluated"}
FAIL_CODES = {"page_health": "navigation_failed"}  # realistic codes where it matters


def result(check_key: str, status: Status) -> CheckResult:
    code = FAIL_CODES.get(check_key, CODES[status]) if status == "fail" else CODES[status]
    return CheckResult(check_key=check_key, status=status, code=code, summary=f"{status}")


async def new_site(pool: Pool) -> Site:
    site_id = await insert_site(pool, await insert_user(pool))
    async with pool.connection() as conn:
        site = await get_site(conn, site_id)
    assert site is not None
    return site


async def observe(
    pool: Pool, site: Site, at: datetime, **statuses_by_device: dict[str, Status]
) -> None:
    """Simulate one finished capture job at time `at`, exactly as the worker saves it."""
    job_id = await insert_job(pool, status="succeeded")
    per_device: list[list[CheckResult]] = []
    async with pool.connection() as conn, conn.transaction():
        for device, statuses in statuses_by_device.items():
            cursor = await conn.execute(
                "INSERT INTO check_runs (site_id, job_id, device, started_at, status) "
                "VALUES (%s, %s, %s, %s, 'completed') RETURNING id",
                (site.id, job_id, device, at),
            )
            row = await cursor.fetchone()
            assert row is not None
            results = [result(key, status) for key, status in statuses.items()]
            for r in results:
                await conn.execute(
                    "INSERT INTO check_results (run_id, check_key, status, code, summary) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (row["id"], r.check_key, r.status, r.code, r.summary),
                )
            per_device.append(results)
        await apply_results(conn, site, worst_results(per_device), at, SETTINGS)


async def meta(pool: Pool, site: Site, at: datetime, status: Status) -> None:
    await observe(pool, site, at, mobile={"meta_pixel": status})


async def alerts(pool: Pool) -> list[dict[str, object]]:
    async with pool.connection() as conn:
        cursor = await conn.execute(
            "SELECT a.kind, a.subject, a.body_text, "
            "EXISTS (SELECT 1 FROM jobs j WHERE j.type = 'send_alert' "
            "        AND (j.payload->>'alert_id')::bigint = a.id) AS has_send_job "
            "FROM alerts a ORDER BY a.id"
        )
        return [dict(row) for row in await cursor.fetchall()]


async def confirm_jobs(pool: Pool) -> list[dict[str, object]]:
    async with pool.connection() as conn:
        cursor = await conn.execute(
            "SELECT payload, priority, extract(epoch FROM run_at - now()) AS due_in_s "
            "FROM jobs WHERE payload->>'reason' = 'confirm'"
        )
        return [dict(row) for row in await cursor.fetchall()]


async def state_of(pool: Pool, site: Site, check_key: str) -> str:
    async with pool.connection() as conn:
        cursor = await conn.execute(
            "SELECT state FROM site_check_states WHERE site_id = %s AND check_key = %s",
            (site.id, check_key),
        )
        row = await cursor.fetchone()
    assert row is not None
    return str(row["state"])


# -- PRD §13 scenarios ---------------------------------------------------------------------------


async def test_flaky_failure_sends_no_alert(db: Pool) -> None:
    site = await new_site(db)
    await meta(db, site, T0, "pass")
    await meta(db, site, T0 + timedelta(hours=1), "fail")
    assert await state_of(db, site, "meta_pixel") == "suspect"
    (confirm,) = await confirm_jobs(db)
    assert confirm["payload"] == {"site_id": str(site.id), "reason": "confirm"}
    assert confirm["priority"] == 50
    assert 590 <= float(confirm["due_in_s"]) <= 600  # type: ignore[arg-type]

    await meta(db, site, T0 + timedelta(hours=1, minutes=10), "pass")  # the confirm check

    assert await state_of(db, site, "meta_pixel") == "healthy"
    assert await alerts(db) == []


async def test_persistent_failure_sends_one_alert_and_one_reminder(db: Pool) -> None:
    site = await new_site(db)
    for offset in (
        timedelta(0),
        timedelta(minutes=10),  # confirmed -> failure alert
        timedelta(hours=12),
        timedelta(hours=36),  # >= 24 h since the alert -> reminder
        timedelta(hours=48),  # 12 h since the reminder -> nothing
    ):
        await meta(db, site, T0 + offset, "fail")

    sent = await alerts(db)
    assert [a["kind"] for a in sent] == ["failure", "reminder"]
    assert all(a["has_send_job"] for a in sent)  # the outbox: every alert has its delivery job
    assert sent[0]["subject"] == "Your Meta Pixel stopped firing on shop.example.com"
    assert str(sent[1]["subject"]).startswith("Still broken: ")


async def test_recovery_sends_one_recovery_alert(db: Pool) -> None:
    site = await new_site(db)
    await meta(db, site, T0, "fail")
    await meta(db, site, T0 + timedelta(minutes=10), "fail")
    await meta(db, site, T0 + timedelta(hours=3), "pass")
    await meta(db, site, T0 + timedelta(hours=4), "pass")  # stays healthy, no more mail

    sent = await alerts(db)
    assert [a["kind"] for a in sent] == ["failure", "recovery"]
    assert "It had been failing for 3 hours." in str(sent[1]["body_text"])


# -- beyond the PRD scenarios --------------------------------------------------------------


async def test_one_confirm_check_even_if_several_checks_fail_at_once(db: Pool) -> None:
    site = await new_site(db)
    await observe(
        db, site, T0, mobile={"meta_pixel": "fail", "google_ga4": "fail", "page_speed": "fail"}
    )
    assert len(await confirm_jobs(db)) == 1


async def test_a_page_outage_alerts_once(db: Pool) -> None:
    site = await new_site(db)
    down: dict[str, Status] = {
        "page_health": "fail",
        "meta_pixel": "error",
        "google_ga4": "error",
        "page_speed": "error",
    }
    await observe(db, site, T0, mobile=down)
    await observe(db, site, T0 + timedelta(minutes=10), mobile=down)

    sent = await alerts(db)
    assert [a["kind"] for a in sent] == ["failure"]
    assert sent[0]["subject"] == "Your landing page is down on shop.example.com"


async def test_broken_on_mobile_only_still_counts(db: Pool) -> None:
    site = await new_site(db)
    await observe(db, site, T0, mobile={"meta_pixel": "fail"}, desktop={"meta_pixel": "pass"})
    assert await state_of(db, site, "meta_pixel") == "suspect"


async def test_email_says_when_it_last_worked(db: Pool) -> None:
    site = await new_site(db)
    await meta(db, site, T0, "pass")
    await meta(db, site, T0 + timedelta(days=1), "fail")
    await meta(db, site, T0 + timedelta(days=1, minutes=10), "fail")
    (alert,) = await alerts(db)
    assert "Last worked: Tue 01 Sep 2026, 09:00 UTC." in str(alert["body_text"])
    assert "Failing since: Wed 02 Sep 2026, 09:00 UTC." in str(alert["body_text"])
    assert f"See details: https://app.example/sites/{site.id}" in str(alert["body_text"])


async def test_failure_history_finds_the_latest_streak(db: Pool) -> None:
    site = await new_site(db)
    timeline: list[Status] = ["pass", "fail", "pass", "fail", "error", "fail"]
    for hour, status in enumerate(timeline):
        await observe(db, site, T0 + timedelta(hours=hour), mobile={"meta_pixel": status})

    async with db.connection() as conn:
        history = await failure_history(conn, site.id, "meta_pixel")
    assert history.last_worked_at == T0 + timedelta(hours=2)
    # The "error" run (page didn't load) neither ends nor restarts the streak.
    assert history.failing_since == T0 + timedelta(hours=3)


def test_dedupe_keys_are_stable_per_incident_and_unique_per_reminder() -> None:
    site_id = UUID("00000000-0000-0000-0000-000000000001")
    site = Site.model_validate(
        {
            "id": site_id,
            "user_id": site_id,
            "name": "s",
            "url": "https://s.example",
            "registrable_domain": "s.example",
            "check_interval_minutes": 60,
            "paused": False,
            "alert_email": "a@s.example",
            "expected_meta_pixel_ids": [],
            "expected_ga4_ids": [],
            "expected_google_ads_ids": [],
            "next_check_at": T0,
        }
    )
    suspect = CheckState(state="suspect", consecutive_fails=1, state_entered_at=T0)
    failure = transition(suspect, "fail", T0 + timedelta(minutes=10))
    assert failure is not None
    key = alert_dedupe_key(site, "meta_pixel", suspect, failure)
    assert key == f"{site_id}:meta_pixel:failure:2026-09-01T09:10:00+00:00"
    # Re-applying the same transition (a retried job) produces the same key -> no duplicate.
    assert alert_dedupe_key(site, "meta_pixel", suspect, failure) == key

    first = transition(failure.new_state, "fail", T0 + timedelta(hours=25))
    assert first is not None and first.alert == "reminder"
    second = transition(first.new_state, "fail", T0 + timedelta(hours=50))
    assert second is not None and second.alert == "reminder"
    assert alert_dedupe_key(site, "meta_pixel", failure.new_state, first) != alert_dedupe_key(
        site, "meta_pixel", first.new_state, second
    )
