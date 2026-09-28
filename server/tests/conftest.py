"""Shared fixtures.

Tests run against the real Postgres from docker compose, but never against the dev database:
the session fixture creates a separate `<dbname>_test` database from scratch, migrates it, and
each test that asks for `db` starts from empty tables.
"""

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from psycopg import AsyncConnection, sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from tagmonitor.config import get_settings
from tagmonitor.db.migrate import migrate
from tagmonitor.db.pool import Pool, create_pool


async def create_database(name: str) -> str:
    """Create (or recreate) a database next to the dev one and return its connection string."""
    main_url = get_settings().database_url
    async with await AsyncConnection.connect(main_url, autocommit=True) as conn:
        await conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
        )
        await conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    return make_conninfo(main_url, dbname=name)


async def drop_database(name: str) -> None:
    async with await AsyncConnection.connect(get_settings().database_url, autocommit=True) as conn:
        await conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
        )


@pytest.fixture(scope="session")
async def database_url() -> str:
    main_db = conninfo_to_dict(get_settings().database_url)["dbname"]
    url = await create_database(f"{main_db}_test")
    await migrate(url)
    return url


@pytest.fixture(scope="session")
async def pool(database_url: str) -> AsyncIterator[Pool]:
    async with create_pool(database_url) as pool:
        yield pool


@pytest.fixture
async def db(pool: Pool) -> Pool:
    """The test pool, with every table emptied first so tests can't see each other's rows."""
    async with pool.connection() as conn:
        cursor = await conn.execute(
            "SELECT tablename FROM pg_tables "
            "WHERE schemaname = 'public' AND tablename <> 'schema_migrations'"
        )
        tables = [sql.Identifier(row["tablename"]) for row in await cursor.fetchall()]
        await conn.execute(
            sql.SQL("TRUNCATE {} RESTART IDENTITY CASCADE").format(sql.SQL(", ").join(tables))
        )
    return pool


@pytest.fixture
async def empty_database_url() -> AsyncIterator[str]:
    """A brand-new database with no schema, dropped after the test."""
    name = f"tagmonitor_scratch_{uuid4().hex[:12]}"
    url = await create_database(name)
    try:
        yield url
    finally:
        await drop_database(name)
