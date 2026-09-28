"""Tiny helpers that insert rows with sensible defaults, so tests only spell out what matters."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from tagmonitor.db.pool import Pool


async def insert_user(pool: Pool, email: str | None = None) -> UUID:
    async with pool.connection() as conn:
        cursor = await conn.execute(
            "INSERT INTO users (email, password_hash) VALUES (%s, 'not-a-real-hash') RETURNING id",
            (email or f"{uuid4().hex[:8]}@example.com",),
        )
        row = await cursor.fetchone()
    assert row is not None
    return UUID(str(row["id"]))


async def insert_site(
    pool: Pool,
    user_id: UUID,
    url: str = "https://shop.example.com/",
    **columns: Any,
) -> UUID:
    values: dict[str, Any] = {
        "user_id": user_id,
        "name": "Example shop",
        "url": url,
        "normalized_url": url,
        "registrable_domain": "example.com",
        "alert_email": "owner@example.com",
    } | columns
    # Column names come from this test file, never from user input.
    names = ", ".join(values)
    placeholders = ", ".join(["%s"] * len(values))
    async with pool.connection() as conn:
        cursor = await conn.execute(
            f"INSERT INTO sites ({names}) VALUES ({placeholders}) RETURNING id",  # noqa: S608
            tuple(values.values()),
        )
        row = await cursor.fetchone()
    assert row is not None
    return UUID(str(row["id"]))


async def insert_job(
    pool: Pool,
    *,
    type: str = "capture_and_check",
    status: str = "queued",
    dedupe_key: str | None = None,
    payload: dict[str, Any] | None = None,
) -> int:
    async with pool.connection() as conn:
        cursor = await conn.execute(
            "INSERT INTO jobs (type, status, dedupe_key, payload) VALUES (%s, %s, %s, %s) "
            "RETURNING id",
            (type, status, dedupe_key, Jsonb(payload or {})),
        )
        row = await cursor.fetchone()
    assert row is not None
    return int(row["id"])


async def insert_run(
    pool: Pool,
    *,
    site_id: UUID | None,
    job_id: int | None = None,
    device: str = "mobile",
    status: str = "completed",
    scan_id: UUID | None = None,
    error_code: str | None = None,
) -> int:
    async with pool.connection() as conn:
        cursor = await conn.execute(
            "INSERT INTO check_runs (site_id, scan_id, job_id, device, started_at, status, "
            "error_code) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (site_id, scan_id, job_id, device, datetime.now(UTC), status, error_code),
        )
        row = await cursor.fetchone()
    assert row is not None
    return int(row["id"])


async def insert_result(
    pool: Pool, run_id: int, check_key: str, status: str, code: str = "test"
) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO check_results (run_id, check_key, status, code, summary) "
            "VALUES (%s, %s, %s, %s, %s)",
            (run_id, check_key, status, code, f"{check_key} is {status}"),
        )
