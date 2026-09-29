"""Runs, run details with screenshots, job polling, trends, alerts, and the ops endpoint."""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from psycopg.types.json import Jsonb

from tagmonitor.db.pool import Pool
from tagmonitor.storage import ObjectStorage
from tests.api_helpers import make_app, signed_up


@pytest.fixture
def app(db: Pool, storage: ObjectStorage) -> FastAPI:
    return make_app(db, storage)


async def site_with_history(
    browser: httpx.AsyncClient, db: Pool, runs: int
) -> tuple[str, list[int]]:
    site = (await browser.post("/api/sites", json={"url": "https://shop.example.com/"})).json()
    run_ids = []
    start = datetime(2026, 9, 1, tzinfo=UTC)
    async with db.connection() as conn:
        for i in range(runs):
            cursor = await conn.execute(
                "INSERT INTO check_runs (site_id, device, started_at, status, http_status, "
                "screenshot_key) VALUES (%s, 'mobile', %s, 'completed', 200, %s) RETURNING id",
                (site["id"], start + timedelta(hours=i), f"test/shot-{i}.jpg"),
            )
            row = await cursor.fetchone()
            assert row is not None
            run_ids.append(row["id"])
            await conn.execute(
                "INSERT INTO check_results (run_id, check_key, status, code, summary, details) "
                "VALUES (%s, 'page_speed', 'pass', 'fast', 'fast', %s)",
                (row["id"], Jsonb({"lcp_ms": 1000.0 + i})),
            )
    return site["id"], run_ids


async def test_run_history_pages_through_every_run_once(app: FastAPI, db: Pool) -> None:
    browser = await signed_up(app)
    site_id, run_ids = await site_with_history(browser, db, runs=5)

    seen: list[int] = []
    cursor = None
    pages = 0
    while True:
        params: dict[str, Any] = {"limit": 2} | ({"cursor": cursor} if cursor else {})
        page = (await browser.get(f"/api/sites/{site_id}/runs", params=params)).json()
        seen += [run["id"] for run in page["runs"]]
        pages += 1
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert pages == 3
    assert seen == list(reversed(run_ids))  # newest first, no gaps, no repeats
    first = (await browser.get(f"/api/sites/{site_id}/runs")).json()["runs"][0]
    assert first["results"] == [{"check_key": "page_speed", "status": "pass", "code": "fast"}]


async def test_bad_cursor(app: FastAPI, db: Pool) -> None:
    browser = await signed_up(app)
    site_id, _ = await site_with_history(browser, db, runs=1)
    response = await browser.get(f"/api/sites/{site_id}/runs", params={"cursor": "garbage"})
    assert response.status_code == 422


async def test_run_detail_links_the_screenshot(
    app: FastAPI, db: Pool, storage: ObjectStorage
) -> None:
    browser = await signed_up(app)
    _, run_ids = await site_with_history(browser, db, runs=1)
    await storage.put("test/shot-0.jpg", b"\xff\xd8jpeg", "image/jpeg")

    run = (await browser.get(f"/api/runs/{run_ids[0]}")).json()
    assert run["results"][0]["title"] == "Mobile speed"
    assert run["results"][0]["explanation"]["meaning"].startswith("The main content appeared")
    assert "X-Amz-Expires=600" in run["screenshot_url"]  # a 10-minute presigned link
    assert run["capture_url"] is None


async def test_job_polling(app: FastAPI) -> None:
    browser = await signed_up(app)
    site = (await browser.post("/api/sites", json={"url": "https://shop.example.com/"})).json()
    job = (await browser.get(f"/api/jobs/{site['active_job']['id']}")).json()
    assert (job["type"], job["status"], job["reason"]) == ("capture_and_check", "queued", "manual")


async def test_lcp_trend(app: FastAPI, db: Pool) -> None:
    browser = await signed_up(app)
    site_id, _ = await site_with_history(browser, db, runs=3)
    async with db.connection() as conn:  # make the history recent
        await conn.execute("UPDATE check_runs SET started_at = started_at + (now() - '2026-09-01')")
    trend = (await browser.get(f"/api/sites/{site_id}/trends", params={"days": 30})).json()
    assert trend["unit"] == "ms"
    assert [p["value"] for p in trend["points"]] == [1000.0, 1001.0, 1002.0]
    bad = await browser.get(f"/api/sites/{site_id}/trends", params={"metric": "cls"})
    assert bad.status_code == 422


async def test_alert_history(app: FastAPI, db: Pool) -> None:
    browser = await signed_up(app)
    site = (await browser.post("/api/sites", json={"url": "https://shop.example.com/"})).json()
    async with db.connection() as conn:
        await conn.execute(
            "INSERT INTO alerts (site_id, check_key, kind, dedupe_key, subject, body_text, "
            "body_html) VALUES (%s, 'meta_pixel', 'failure', 'k1', 'Pixel down', 't', 'h')",
            (site["id"],),
        )
    (alert,) = (await browser.get("/api/alerts")).json()
    assert (alert["site_name"], alert["kind"], alert["subject"]) == (
        "shop.example.com",
        "failure",
        "Pixel down",
    )


async def test_ops_queue_is_admin_only(app: FastAPI, db: Pool) -> None:
    browser = await signed_up(app, "ops@shop.example")
    assert (await browser.get("/api/ops/queue")).status_code == 403
    async with db.connection() as conn:
        await conn.execute("UPDATE users SET is_admin = true")
        await conn.execute(
            "INSERT INTO jobs (type, status, locked_at, finished_at, last_error) VALUES "
            "('capture_and_check', 'succeeded', now() - interval '4 seconds', now(), NULL), "
            "('capture_and_check', 'dead', now() - interval '1 second', now(), 'boom'), "
            "('send_alert', 'queued', NULL, NULL, NULL)"
        )
    stats = (await browser.get("/api/ops/queue")).json()
    assert {"type": "send_alert", "status": "queued", "count": 1} in stats["depth"]
    assert stats["last_24h"]["succeeded"] == 1 and stats["last_24h"]["dead"] == 1
    assert stats["last_24h"]["success_rate"] == 0.5
    assert 3.5 < stats["last_24h"]["p50_s"] < 5
    assert stats["dead_jobs"][0]["last_error"] == "boom"
