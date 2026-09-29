"""Tenant isolation: another account can't see or touch your sites, runs, jobs or alerts."""

import pytest
from fastapi import FastAPI

from tagmonitor.db.pool import Pool
from tagmonitor.storage import ObjectStorage
from tests.api_helpers import make_app, signed_up
from tests.factories import insert_run


@pytest.fixture
def app(db: Pool, storage: ObjectStorage) -> FastAPI:
    return make_app(db, storage)


async def test_user_b_cannot_reach_user_a_data(app: FastAPI, db: Pool) -> None:
    alice = await signed_up(app, "alice@shop.example")
    bob = await signed_up(app, "bob@other.example")
    site = (await alice.post("/api/sites", json={"url": "https://shop.example.com/"})).json()
    site_id, job_id = site["id"], site["active_job"]["id"]
    run_id = await insert_run(db, site_id=site_id)
    async with db.connection() as conn:
        await conn.execute(
            "INSERT INTO alerts (site_id, check_key, kind, dedupe_key, subject, body_text, "
            "body_html) VALUES (%s, 'meta_pixel', 'failure', 'k', 'Alice only', 't', 'h')",
            (site_id,),
        )

    # Everything answers 404 for Bob, exactly as for an id that doesn't exist.
    assert (await bob.get(f"/api/sites/{site_id}")).status_code == 404
    assert (await bob.patch(f"/api/sites/{site_id}", json={"name": "pwned"})).status_code == 404
    assert (await bob.post(f"/api/sites/{site_id}/check-now")).status_code == 404
    assert (await bob.delete(f"/api/sites/{site_id}")).status_code == 404
    assert (await bob.get(f"/api/sites/{site_id}/runs")).status_code == 404
    assert (await bob.get(f"/api/sites/{site_id}/trends")).status_code == 404
    assert (await bob.get(f"/api/runs/{run_id}")).status_code == 404
    assert (await bob.get(f"/api/jobs/{job_id}")).status_code == 404
    assert (await bob.get("/api/sites")).json() == []
    assert (await bob.get("/api/alerts")).json() == []

    # ...and nothing changed for Alice.
    mine = (await alice.get(f"/api/sites/{site_id}")).json()
    assert mine["name"] == "shop.example.com"
    assert [a["subject"] for a in (await alice.get("/api/alerts")).json()] == ["Alice only"]
    assert (await alice.get(f"/api/runs/{run_id}")).status_code == 200


async def test_both_users_may_monitor_the_same_page(app: FastAPI) -> None:
    alice = await signed_up(app)
    bob = await signed_up(app)
    url = {"url": "https://shop.example.com/"}
    assert (await alice.post("/api/sites", json=url)).status_code == 201
    assert (await bob.post("/api/sites", json=url)).status_code == 201
