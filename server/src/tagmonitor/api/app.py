"""The FastAPI application. Run with: uvicorn tagmonitor.api.app:app"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from tagmonitor.api import health
from tagmonitor.config import get_settings
from tagmonitor.db.pool import create_pool


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    pool = create_pool(settings.database_url)
    await pool.open()
    app.state.pool = pool
    try:
        yield
    finally:
        await pool.close()


def create_app() -> FastAPI:
    """Build the app without opening resources, so tests can attach a test pool to app.state."""
    app = FastAPI(title="tag-monitor", lifespan=lifespan)
    app.include_router(health.router)
    return app


app = create_app()
