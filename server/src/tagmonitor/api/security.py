"""Passwords, sessions and the login rate limit (PRD §17)."""

import asyncio
import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Request

from tagmonitor.queue.jobs import Conn

SESSION_COOKIE = "tm_session"
SESSION_TTL = timedelta(days=30)
# Sliding expiry: an active session is extended, but at most once an hour, so browsing the
# dashboard doesn't write to the sessions table on every request.
SESSION_REFRESH_AFTER = timedelta(hours=1)

LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW = timedelta(minutes=15)

# argon2id with the library's current recommended cost. Hashing is deliberately slow (tens of
# milliseconds of CPU), so it runs in a thread instead of blocking the event loop.
_hasher = PasswordHasher()
# Verified against when the email doesn't exist, so "no such user" takes as long as "wrong
# password" and response times don't reveal which emails have accounts.
_DUMMY_HASH = _hasher.hash("tag-monitor timing-equalizer password")


@dataclass(frozen=True)
class CurrentUser:
    id: UUID
    email: str
    is_admin: bool


async def hash_password(password: str) -> str:
    return await asyncio.to_thread(_hasher.hash, password)


async def verify_password(stored_hash: str | None, password: str) -> bool:
    def verify() -> bool:
        try:
            _hasher.verify(stored_hash or _DUMMY_HASH, password)
        except (VerificationError, InvalidHashError):
            return False
        return stored_hash is not None

    return await asyncio.to_thread(verify)


def needs_rehash(stored_hash: str) -> bool:
    """True when the hash used weaker parameters than today's defaults."""
    return _hasher.check_needs_rehash(stored_hash)


def _token_hash(token: str) -> bytes:
    """Only the SHA-256 of a session token is stored, so a database leak doesn't hand out
    working sessions. (Tokens are 256-bit random, so a plain fast hash is enough here, unlike
    passwords.)"""
    return hashlib.sha256(token.encode()).digest()


async def create_session(conn: Conn, user_id: UUID) -> str:
    token = secrets.token_urlsafe(32)  # 32 random bytes
    await conn.execute(
        "INSERT INTO sessions (user_id, token_hash, expires_at) VALUES (%s, %s, now() + %s)",
        (user_id, _token_hash(token), SESSION_TTL),
    )
    return token


async def user_for_session(conn: Conn, token: str) -> CurrentUser | None:
    cursor = await conn.execute(
        """
        SELECT u.id, u.email, u.is_admin, s.id AS session_id,
               s.last_seen_at < now() - %s AS needs_refresh
        FROM sessions s JOIN users u ON u.id = s.user_id
        WHERE s.token_hash = %s AND s.expires_at > now()
        """,
        (SESSION_REFRESH_AFTER, _token_hash(token)),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    if row["needs_refresh"]:
        await conn.execute(
            "UPDATE sessions SET last_seen_at = now(), expires_at = now() + %s WHERE id = %s",
            (SESSION_TTL, row["session_id"]),
        )
    return CurrentUser(id=row["id"], email=str(row["email"]), is_admin=row["is_admin"])


async def delete_session(conn: Conn, token: str) -> None:
    await conn.execute("DELETE FROM sessions WHERE token_hash = %s", (_token_hash(token),))


def client_ip(request: Request, trust_proxy_headers: bool) -> str:
    """The visitor's IP. Behind our own Next.js proxy the socket peer is the proxy, so we read
    X-Forwarded-For, but only when configured to, and only its last entry: the one our proxy
    appended. Earlier entries are whatever the client claimed and can't be trusted."""
    forwarded = request.headers.get("x-forwarded-for")
    if trust_proxy_headers and forwarded:
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def login_key(email: str, ip: str) -> str:
    return f"{email.strip().lower()}|{ip}"


async def login_retry_after(conn: Conn, key: str) -> int | None:
    """Seconds until this email+IP may try again, or None if it's not limited right now."""
    cursor = await conn.execute(
        """
        SELECT count(*) AS failures,
               extract(epoch FROM min(attempted_at) + %(window)s - now()) AS retry_after
        FROM login_attempts
        WHERE key = %(key)s AND attempted_at > now() - %(window)s
        """,
        {"key": key, "window": LOGIN_WINDOW},
    )
    row = await cursor.fetchone()
    if row is None or row["failures"] < LOGIN_MAX_FAILURES:
        return None
    return max(1, int(row["retry_after"]) + 1)


async def record_failed_login(conn: Conn, key: str) -> None:
    await conn.execute(
        "DELETE FROM login_attempts WHERE key = %s AND attempted_at <= now() - %s",
        (key, LOGIN_WINDOW),
    )
    await conn.execute("INSERT INTO login_attempts (key) VALUES (%s)", (key,))


async def clear_failed_logins(conn: Conn, key: str) -> None:
    await conn.execute("DELETE FROM login_attempts WHERE key = %s", (key,))
