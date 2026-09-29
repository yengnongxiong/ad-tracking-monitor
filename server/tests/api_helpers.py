"""Build the API against the test database, MinIO bucket and a fake DNS table."""

from uuid import uuid4

import httpx
from fastapi import FastAPI

from tagmonitor.api.app import create_app
from tagmonitor.config import get_settings
from tagmonitor.db.pool import Pool
from tagmonitor.storage import ObjectStorage
from tests.fixture_server import StaticResolver

# What "DNS" says during API tests: two public sites and one that resolves to a private IP.
API_RESOLVER = StaticResolver(
    {
        "shop.example.com": ["93.184.215.14"],
        "www.shop.example.com": ["93.184.215.14"],
        "other.example.com": ["93.184.215.15"],
        "internal.example.com": ["10.0.0.5"],
    }
)


# Product defaults, pinned so values tuned in a developer's .env (the demo shortens the
# check-now cooldown) can't change what the tests assert.
PRODUCT_DEFAULTS = {"check_now_cooldown_seconds": 300, "trust_proxy_headers": False}


def make_app(pool: Pool, storage: ObjectStorage, **settings: object) -> FastAPI:
    app = create_app(get_settings().model_copy(update=PRODUCT_DEFAULTS | settings))
    app.state.pool = pool
    app.state.storage = storage
    app.state.resolver = API_RESOLVER
    return app


def client(app: FastAPI, ip: str = "203.0.113.7", csrf: bool = True) -> httpx.AsyncClient:
    """A browser-like client: keeps cookies, sends the header our frontend sends."""
    headers = {"X-Requested-With": "tag-monitor"} if csrf else {}
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=(ip, 51000)),
        base_url="http://test",
        headers=headers,
    )


async def signed_up(app: FastAPI, email: str | None = None) -> httpx.AsyncClient:
    browser = client(app)
    response = await browser.post(
        "/api/auth/signup",
        json={"email": email or f"{uuid4().hex[:8]}@shop.example", "password": "correct horse"},
    )
    assert response.status_code == 201, response.text
    return browser
