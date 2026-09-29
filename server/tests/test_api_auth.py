"""Sign-up, login, sessions, CSRF and the login rate limit (PRD §17)."""

import hashlib

import pytest
from fastapi import FastAPI

from tagmonitor.db.pool import Pool
from tagmonitor.storage import ObjectStorage
from tests.api_helpers import client, make_app, signed_up

PASSWORD = "correct horse"


@pytest.fixture
def app(db: Pool, storage: ObjectStorage) -> FastAPI:
    return make_app(db, storage)


async def test_signup_logs_you_in_with_a_safe_cookie(app: FastAPI, db: Pool) -> None:
    async with client(app) as browser:
        response = await browser.post(
            "/api/auth/signup", json={"email": "Maria@Shop.example", "password": PASSWORD}
        )
        assert response.status_code == 201
        cookie = response.headers["set-cookie"]
        assert "tm_session=" in cookie
        assert "HttpOnly" in cookie
        assert "SameSite=lax" in cookie
        assert "Max-Age=2592000" in cookie  # 30 days
        me = await browser.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "Maria@Shop.example"

    async with db.connection() as conn:
        row = await (await conn.execute("SELECT password_hash FROM users")).fetchone()
        session = await (await conn.execute("SELECT token_hash FROM sessions")).fetchone()
    assert row is not None and row["password_hash"].startswith("$argon2id$")
    assert session is not None
    token = cookie.split("tm_session=")[1].split(";")[0]
    assert bytes(session["token_hash"]) == hashlib.sha256(token.encode()).digest()
    assert len(token) >= 43  # 32 random bytes, base64url


async def test_emails_are_unique_ignoring_case(app: FastAPI) -> None:
    await signed_up(app, "owner@shop.example")
    async with client(app) as browser:
        response = await browser.post(
            "/api/auth/signup", json={"email": "OWNER@shop.example", "password": PASSWORD}
        )
    assert response.status_code == 409


@pytest.mark.parametrize(
    "body",
    [
        {"email": "not-an-email", "password": PASSWORD},
        {"email": "a@shop.example", "password": "short"},
        {"email": "a@shop.example"},
    ],
)
async def test_signup_validation(app: FastAPI, body: dict[str, str]) -> None:
    async with client(app) as browser:
        assert (await browser.post("/api/auth/signup", json=body)).status_code == 422


async def test_login_logout_and_wrong_password(app: FastAPI) -> None:
    await signed_up(app, "owner@shop.example")
    async with client(app) as browser:
        wrong = await browser.post(
            "/api/auth/login", json={"email": "owner@shop.example", "password": "nope nope"}
        )
        unknown = await browser.post(
            "/api/auth/login", json={"email": "ghost@shop.example", "password": PASSWORD}
        )
        assert wrong.status_code == unknown.status_code == 401
        assert wrong.json() == unknown.json()  # doesn't reveal which emails exist

        ok = await browser.post(
            "/api/auth/login", json={"email": "owner@shop.example", "password": PASSWORD}
        )
        assert ok.status_code == 200
        assert (await browser.get("/api/auth/me")).status_code == 200
        assert (await browser.post("/api/auth/logout")).status_code == 204
        assert (await browser.get("/api/auth/me")).status_code == 401


async def test_requests_without_a_session_are_rejected(app: FastAPI) -> None:
    async with client(app) as browser:
        assert (await browser.get("/api/sites")).status_code == 401
        browser.cookies.set("tm_session", "forged-token")
        assert (await browser.get("/api/sites")).status_code == 401


async def test_expired_sessions_stop_working(app: FastAPI, db: Pool) -> None:
    browser = await signed_up(app)
    async with db.connection() as conn:
        await conn.execute("UPDATE sessions SET expires_at = now() - interval '1 second'")
    assert (await browser.get("/api/auth/me")).status_code == 401


async def test_sessions_slide_forward_when_used(app: FastAPI, db: Pool) -> None:
    browser = await signed_up(app)
    async with db.connection() as conn:
        await conn.execute(
            "UPDATE sessions SET expires_at = now() + interval '1 day', "
            "last_seen_at = now() - interval '2 hours'"
        )
    assert (await browser.get("/api/auth/me")).status_code == 200
    async with db.connection() as conn:
        row = await (
            await conn.execute(
                "SELECT expires_at - now() > interval '29 days' AS renewed FROM sessions"
            )
        ).fetchone()
    assert row is not None and row["renewed"]


async def test_mutations_need_the_csrf_header(app: FastAPI) -> None:
    async with client(app, csrf=False) as browser:
        response = await browser.post(
            "/api/auth/signup", json={"email": "a@shop.example", "password": PASSWORD}
        )
        assert response.status_code == 403
        assert (await browser.get("/api/health")).status_code == 200  # reads are fine


async def test_login_rate_limit_per_email_and_ip(app: FastAPI) -> None:
    await signed_up(app, "owner@shop.example")
    creds = {"email": "owner@shop.example", "password": "wrong password"}
    async with client(app, ip="198.51.100.1") as attacker:
        for _ in range(5):
            assert (await attacker.post("/api/auth/login", json=creds)).status_code == 401
        blocked = await attacker.post(
            "/api/auth/login", json={"email": "owner@shop.example", "password": PASSWORD}
        )
        assert blocked.status_code == 429  # even the right password is refused for a while
        assert 0 < int(blocked.headers["retry-after"]) <= 15 * 60

    async with client(app, ip="198.51.100.2") as owner_elsewhere:
        ok = await owner_elsewhere.post(
            "/api/auth/login", json={"email": "owner@shop.example", "password": PASSWORD}
        )
        assert ok.status_code == 200


async def test_a_successful_login_resets_the_counter(app: FastAPI) -> None:
    await signed_up(app, "owner@shop.example")
    wrong = {"email": "owner@shop.example", "password": "wrong password"}
    right = {"email": "owner@shop.example", "password": PASSWORD}
    async with client(app) as browser:
        for _ in range(4):
            await browser.post("/api/auth/login", json=wrong)
        assert (await browser.post("/api/auth/login", json=right)).status_code == 200
        for _ in range(4):
            await browser.post("/api/auth/login", json=wrong)
        assert (await browser.post("/api/auth/login", json=right)).status_code == 200


async def test_proxy_headers_only_count_when_trusted(db: Pool, storage: ObjectStorage) -> None:
    """Behind our Next.js proxy the real IP is the last X-Forwarded-For hop."""
    trusted = make_app(db, storage, trust_proxy_headers=True)
    await signed_up(trusted, "owner@shop.example")
    wrong = {"email": "owner@shop.example", "password": "wrong password"}
    async with client(trusted) as browser:
        for _ in range(5):
            await browser.post(
                "/api/auth/login", json=wrong, headers={"X-Forwarded-For": "1.1.1.1, 192.0.2.9"}
            )
        other_client = await browser.post(
            "/api/auth/login", json=wrong, headers={"X-Forwarded-For": "1.1.1.1, 192.0.2.10"}
        )
    assert other_client.status_code == 401  # a different real client isn't blocked


async def test_spoofed_forwarded_for_cannot_dodge_the_limit_by_default(app: FastAPI) -> None:
    """Regression: the Next.js proxy passes a client's own X-Forwarded-For straight through,
    so trusting it by default would let an attacker claim a new IP on every attempt."""
    await signed_up(app, "owner@shop.example")
    wrong = {"email": "owner@shop.example", "password": "wrong password"}
    async with client(app) as attacker:
        for i in range(5):
            await attacker.post(
                "/api/auth/login", json=wrong, headers={"X-Forwarded-For": f"10.9.9.{i}"}
            )
        sixth = await attacker.post(
            "/api/auth/login", json=wrong, headers={"X-Forwarded-For": "10.9.9.200"}
        )
    assert sixth.status_code == 429
