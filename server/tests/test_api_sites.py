"""Sites: create (with the SSRF check), list, detail, update, delete, check now."""

from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from tagmonitor.db.pool import Pool
from tagmonitor.storage import ObjectStorage
from tests.api_helpers import make_app, signed_up
from tests.factories import insert_job, insert_result, insert_run


@pytest.fixture
def app(db: Pool, storage: ObjectStorage) -> FastAPI:
    return make_app(db, storage)


async def add_site(browser: httpx.AsyncClient, **fields: Any) -> dict[str, Any]:
    response = await browser.post(
        "/api/sites", json={"url": "https://shop.example.com/spring-sale"} | fields
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def test_adding_a_site_queues_its_first_check(app: FastAPI, db: Pool) -> None:
    browser = await signed_up(app, "maria@shop.example")
    site = await add_site(browser, expected_google_ads_ids=["123456789"])

    assert site["name"] == "shop.example.com"  # defaults to the host
    assert site["alert_email"] == "maria@shop.example"  # defaults to the account email
    assert site["check_interval_minutes"] == 1440
    assert site["expected_google_ads_ids"] == ["AW-123456789"]  # normalized
    assert site["statuses"] == {}
    assert site["active_job"]["status"] == "queued"

    async with db.connection() as conn:
        row = await (
            await conn.execute(
                "SELECT s.normalized_url, s.registrable_domain, j.priority, j.payload "
                "FROM sites s JOIN jobs j ON j.dedupe_key = 'site:' || s.id"
            )
        ).fetchone()
    assert row is not None
    assert row["normalized_url"] == "https://shop.example.com/spring-sale"
    assert row["registrable_domain"] == "example.com"
    assert row["priority"] == 100
    assert row["payload"] == {"site_id": site["id"], "reason": "manual"}


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("https://internal.example.com/", "private network"),  # DNS points inside
        ("http://169.254.169.254/latest/meta-data/", "private network"),  # cloud metadata
        ("http://2130706433/", "private network"),  # 127.0.0.1 in disguise
        ("https://nope.example.com/", "couldn't find that domain"),
        ("ftp://shop.example.com/", "doesn't look like a web address"),
        ("https://shop.example.com:8443/", "doesn't look like a web address"),
        ("https://user:pw@shop.example.com/", "doesn't look like a web address"),
    ],
)
async def test_unsafe_or_invalid_urls_are_refused(app: FastAPI, url: str, message: str) -> None:
    browser = await signed_up(app)
    response = await browser.post("/api/sites", json={"url": url})
    assert response.status_code == 422
    assert message in response.json()["detail"]


async def test_the_same_page_cannot_be_added_twice(app: FastAPI) -> None:
    browser = await signed_up(app)
    await add_site(browser, url="https://shop.example.com/sale?utm_source=fb")
    duplicate = await browser.post(
        "/api/sites", json={"url": "HTTPS://Shop.Example.com:443/sale?fbclid=abc#top"}
    )
    assert duplicate.status_code == 409


@pytest.mark.parametrize(
    "fields",
    [
        {"check_interval_minutes": 30},
        {"alert_email": "not-an-email"},
        {"expected_meta_pixel_ids": ["abc"]},
        {"expected_ga4_ids": ["UA-12345-1"]},
        {"expected_google_ads_ids": ["AW-12"]},
        {"name": ""},
    ],
)
async def test_field_validation(app: FastAPI, fields: dict[str, Any]) -> None:
    browser = await signed_up(app)
    response = await browser.post("/api/sites", json={"url": "https://shop.example.com/"} | fields)
    assert response.status_code == 422


async def test_list_and_detail_show_latest_results_with_explanations(
    app: FastAPI, db: Pool
) -> None:
    browser = await signed_up(app)
    site = await add_site(browser)
    job = await insert_job(db, status="succeeded")
    run = await insert_run(db, site_id=site["id"], job_id=job)
    await insert_result(db, run, "meta_pixel", "fail", code="installed_not_firing")
    await insert_result(db, run, "page_health", "pass", code="ok")

    listed = (await browser.get("/api/sites")).json()
    assert [s["id"] for s in listed] == [site["id"]]
    assert listed[0]["statuses"]["meta_pixel"] == {
        "status": "fail",
        "code": "installed_not_firing",
        "summary": "meta_pixel is fail",
    }
    assert listed[0]["last_checked_at"] is not None

    detail = (await browser.get(f"/api/sites/{site['id']}")).json()
    meta = detail["latest_results"][0]
    assert meta["title"] == "Meta Pixel"
    assert meta["explanation"]["how_to_fix"].startswith("Look for a pixel snippet")
    assert [r["check_key"] for r in detail["latest_results"]] == ["meta_pixel", "page_health"]


async def test_update_site(app: FastAPI, db: Pool) -> None:
    browser = await signed_up(app)
    site = await add_site(browser, ad_headline="Spring sale")
    response = await browser.patch(
        f"/api/sites/{site['id']}",
        json={
            "name": "Spring landing page",
            "check_interval_minutes": 60,
            "paused": True,
            "expected_ga4_ids": ["g-abc1234"],
            "ad_headline": None,  # clears it
            "alert_email": None,  # ignored: can't be empty
        },
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["name"] == "Spring landing page"
    assert updated["check_interval_minutes"] == 60
    assert updated["paused"] is True
    assert updated["expected_ga4_ids"] == ["G-ABC1234"]
    assert updated["ad_headline"] is None
    assert updated["alert_email"] == site["alert_email"]


async def test_changing_the_url_is_checked_again(app: FastAPI) -> None:
    browser = await signed_up(app)
    site = await add_site(browser)
    bad = await browser.patch(f"/api/sites/{site['id']}", json={"url": "http://10.0.0.1/"})
    assert bad.status_code == 422
    good = await browser.patch(
        f"/api/sites/{site['id']}", json={"url": "https://other.example.com/landing"}
    )
    assert good.status_code == 200
    assert good.json()["url"] == "https://other.example.com/landing"


async def test_delete_site(app: FastAPI) -> None:
    browser = await signed_up(app)
    site = await add_site(browser)
    assert (await browser.delete(f"/api/sites/{site['id']}")).status_code == 204
    assert (await browser.get(f"/api/sites/{site['id']}")).status_code == 404
    assert (await browser.delete(f"/api/sites/{site['id']}")).status_code == 404


async def test_check_now_joins_a_pending_check_instead_of_adding_one(app: FastAPI) -> None:
    browser = await signed_up(app)
    site = await add_site(browser)  # its first check is still queued
    response = await browser.post(f"/api/sites/{site['id']}/check-now")
    assert response.status_code == 200
    assert response.json()["job_id"] == site["active_job"]["id"]


async def test_check_now_is_limited_to_once_per_five_minutes(app: FastAPI, db: Pool) -> None:
    browser = await signed_up(app)
    site = await add_site(browser)
    async with db.connection() as conn:  # the first check finished
        await conn.execute("UPDATE jobs SET status = 'succeeded'")

    first = await browser.post(f"/api/sites/{site['id']}/check-now")
    assert first.status_code == 202
    async with db.connection() as conn:
        await conn.execute("UPDATE jobs SET status = 'succeeded'")

    second = await browser.post(f"/api/sites/{site['id']}/check-now")
    assert second.status_code == 429
    assert 290 <= int(second.headers["retry-after"]) <= 301

    async with db.connection() as conn:  # five minutes later
        await conn.execute(
            "UPDATE sites SET last_check_requested_at = now() - interval '301 seconds'"
        )
    assert (await browser.post(f"/api/sites/{site['id']}/check-now")).status_code == 202
