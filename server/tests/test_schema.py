"""The schema's constraints are part of the design: these tests pin down what they guarantee."""

import pytest
from psycopg import errors

from tagmonitor.db.pool import Pool
from tests.factories import insert_job, insert_result, insert_run, insert_site, insert_user


async def count(pool: Pool, table: str) -> int:
    async with pool.connection() as conn:
        # Table names come from the test code only.
        cursor = await conn.execute(f"SELECT count(*) AS n FROM {table}")  # noqa: S608
        row = await cursor.fetchone()
    assert row is not None
    return int(row["n"])


async def test_user_emails_are_unique_ignoring_case(db: Pool) -> None:
    await insert_user(db, "Owner@Example.com")
    with pytest.raises(errors.UniqueViolation):
        await insert_user(db, "owner@example.COM")


async def test_a_user_cannot_add_the_same_url_twice(db: Pool) -> None:
    alice = await insert_user(db)
    bob = await insert_user(db)
    await insert_site(db, alice, "https://shop.example.com/")
    await insert_site(db, bob, "https://shop.example.com/")  # other users may monitor it too
    with pytest.raises(errors.UniqueViolation):
        await insert_site(db, alice, "https://shop.example.com/")


@pytest.mark.parametrize("minutes", [0, 30, 1439, 10_000])
async def test_check_interval_must_be_one_of_the_allowed_values(db: Pool, minutes: int) -> None:
    user = await insert_user(db)
    with pytest.raises(errors.CheckViolation):
        await insert_site(db, user, check_interval_minutes=minutes)


async def test_only_one_active_job_per_dedupe_key(db: Pool) -> None:
    await insert_job(db, dedupe_key="site:1", status="queued")
    with pytest.raises(errors.UniqueViolation):
        await insert_job(db, dedupe_key="site:1", status="running")


async def test_finished_jobs_do_not_block_a_new_job_with_the_same_key(db: Pool) -> None:
    await insert_job(db, dedupe_key="site:1", status="succeeded")
    await insert_job(db, dedupe_key="site:1", status="dead")
    await insert_job(db, dedupe_key="site:1", status="queued")
    assert await count(db, "jobs") == 3


async def test_jobs_without_a_dedupe_key_never_conflict(db: Pool) -> None:
    await insert_job(db, dedupe_key=None)
    await insert_job(db, dedupe_key=None)
    assert await count(db, "jobs") == 2


async def test_job_status_and_type_are_constrained(db: Pool) -> None:
    with pytest.raises(errors.CheckViolation):
        await insert_job(db, status="failed")
    with pytest.raises(errors.CheckViolation):
        await insert_job(db, type="mine_bitcoin")


async def test_a_run_belongs_to_exactly_one_of_site_or_scan(db: Pool) -> None:
    with pytest.raises(errors.CheckViolation):
        await insert_run(db, site_id=None, scan_id=None)


async def test_an_error_run_needs_an_error_code(db: Pool) -> None:
    site = await insert_site(db, await insert_user(db))
    with pytest.raises(errors.CheckViolation):
        await insert_run(db, site_id=site, status="error")
    await insert_run(db, site_id=site, status="error", error_code="browser_crash")


async def test_one_result_per_check_per_run(db: Pool) -> None:
    site = await insert_site(db, await insert_user(db))
    run = await insert_run(db, site_id=site)
    await insert_result(db, run, "meta_pixel", "pass")
    with pytest.raises(errors.UniqueViolation):
        await insert_result(db, run, "meta_pixel", "fail")
    with pytest.raises(errors.CheckViolation):
        await insert_result(db, run, "page_health", "great")


async def test_alert_dedupe_key_is_unique(db: Pool) -> None:
    site = await insert_site(db, await insert_user(db))
    insert = (
        "INSERT INTO alerts (site_id, check_key, kind, dedupe_key, subject, body_text, body_html) "
        "VALUES (%s, 'meta_pixel', 'failure', 'same-key', 's', 't', 'h')"
    )
    async with db.connection() as conn:
        await conn.execute(insert, (site,))
    with pytest.raises(errors.UniqueViolation):
        async with db.connection() as conn:
            await conn.execute(insert, (site,))


async def test_deleting_a_user_removes_everything_they_own(db: Pool) -> None:
    user = await insert_user(db)
    site = await insert_site(db, user)
    run = await insert_run(db, site_id=site)
    await insert_result(db, run, "meta_pixel", "pass")
    async with db.connection() as conn:
        await conn.execute(
            "INSERT INTO site_check_states (site_id, check_key, state) "
            "VALUES (%s, 'meta_pixel', 'healthy')",
            (site,),
        )
        await conn.execute("DELETE FROM users WHERE id = %s", (user,))

    for table in ("sites", "check_runs", "check_results", "site_check_states"):
        assert await count(db, table) == 0, table


async def latest_status(pool: Pool) -> dict[str, str]:
    async with pool.connection() as conn:
        cursor = await conn.execute("SELECT check_key, status FROM site_latest_status")
        return {row["check_key"]: row["status"] for row in await cursor.fetchall()}


async def test_latest_status_uses_the_newest_job_and_the_worst_device(db: Pool) -> None:
    site = await insert_site(db, await insert_user(db))
    old_job = await insert_job(db, status="succeeded")
    new_job = await insert_job(db, status="succeeded")

    old_run = await insert_run(db, site_id=site, job_id=old_job)
    await insert_result(db, old_run, "meta_pixel", "fail")

    # Newest job: the pixel fires on desktop but not on mobile -> the site shows "fail".
    mobile = await insert_run(db, site_id=site, job_id=new_job, device="mobile")
    desktop = await insert_run(db, site_id=site, job_id=new_job, device="desktop")
    await insert_result(db, mobile, "meta_pixel", "fail")
    await insert_result(db, desktop, "meta_pixel", "pass")
    # Health passed on mobile but could not be evaluated on desktop -> "pass" wins over "error".
    await insert_result(db, mobile, "page_health", "pass")
    await insert_result(db, desktop, "page_health", "error")

    assert await latest_status(db) == {"meta_pixel": "fail", "page_health": "pass"}


async def test_latest_status_ignores_older_jobs(db: Pool) -> None:
    site = await insert_site(db, await insert_user(db))
    old_job = await insert_job(db, status="succeeded")
    new_job = await insert_job(db, status="succeeded")
    old_run = await insert_run(db, site_id=site, job_id=old_job)
    new_run = await insert_run(db, site_id=site, job_id=new_job)
    await insert_result(db, old_run, "google_ga4", "fail")
    await insert_result(db, new_run, "google_ga4", "pass")

    assert await latest_status(db) == {"google_ga4": "pass"}


async def test_latest_status_drops_checks_the_newest_job_did_not_run(db: Pool) -> None:
    """Regression: after the owner deleted their ad copy, the last message match verdict stayed
    on the dashboard forever, because the view took each check's newest result from any job."""
    site = await insert_site(db, await insert_user(db))
    old_run = await insert_run(db, site_id=site, job_id=await insert_job(db, status="succeeded"))
    await insert_result(db, old_run, "message_match", "fail")
    await insert_result(db, old_run, "meta_pixel", "pass")
    new_run = await insert_run(db, site_id=site, job_id=await insert_job(db, status="succeeded"))
    await insert_result(db, new_run, "meta_pixel", "pass")

    assert await latest_status(db) == {"meta_pixel": "pass"}


async def test_latest_status_ties_go_to_the_first_device(db: Pool) -> None:
    """Both devices failed: show mobile (captured first), the device the alert email names."""
    site = await insert_site(db, await insert_user(db))
    job = await insert_job(db, status="succeeded")
    mobile = await insert_run(db, site_id=site, job_id=job, device="mobile")
    desktop = await insert_run(db, site_id=site, job_id=job, device="desktop")
    await insert_result(db, desktop, "meta_pixel", "fail")
    await insert_result(db, mobile, "meta_pixel", "fail")
    async with db.connection() as conn:
        row = await (await conn.execute("SELECT device FROM site_latest_status")).fetchone()
    assert row is not None
    assert row["device"] == "mobile"


async def test_latest_status_skips_a_newer_job_that_died(db: Pool) -> None:
    """A dead job leaves an error run with no results; the last real results still show."""
    site = await insert_site(db, await insert_user(db))
    run = await insert_run(db, site_id=site, job_id=await insert_job(db, status="succeeded"))
    await insert_result(db, run, "meta_pixel", "pass")
    await insert_run(
        db,
        site_id=site,
        job_id=await insert_job(db, status="dead"),
        status="error",
        error_code="capture_timeout",
    )

    assert await latest_status(db) == {"meta_pixel": "pass"}


async def test_latest_status_after_retention_nulled_the_job(db: Pool) -> None:
    """Retention deletes old jobs (check_runs.job_id is then NULL) but keeps each site's
    latest run per device, which must still show."""
    site = await insert_site(db, await insert_user(db))
    mobile = await insert_run(db, site_id=site, device="mobile")
    desktop = await insert_run(db, site_id=site, device="desktop")
    await insert_result(db, mobile, "meta_pixel", "pass")
    await insert_result(db, desktop, "meta_pixel", "fail")

    assert await latest_status(db) == {"meta_pixel": "fail"}
