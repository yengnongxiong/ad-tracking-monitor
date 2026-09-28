"""Shared fixtures. Tests talk to the real Postgres from docker compose (DATABASE_URL)."""

from collections.abc import AsyncIterator

import pytest

from tagmonitor.config import get_settings
from tagmonitor.db.pool import Pool, create_pool


@pytest.fixture(scope="session")
async def pool() -> AsyncIterator[Pool]:
    async with create_pool(get_settings().database_url) as pool:
        yield pool
