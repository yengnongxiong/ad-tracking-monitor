"""The FastAPI application. Run with: uvicorn tagmonitor.api.app:app"""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from tagmonitor.api import alerts, auth, health, ops, runs, sites
from tagmonitor.browser.ssrf import SystemResolver
from tagmonitor.config import Settings, get_settings
from tagmonitor.db.pool import create_pool
from tagmonitor.storage import ObjectStorage

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    logging.basicConfig(level=settings.log_level)
    pool = create_pool(settings.database_url)
    await pool.open()
    app.state.pool = pool
    app.state.storage = ObjectStorage(settings)
    app.state.resolver = SystemResolver()
    try:
        yield
    finally:
        await pool.close()


async def require_csrf_header(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """CSRF defense alongside SameSite=Lax cookies (PRD §17).

    A cross-site page can make the browser POST a form with our cookie attached, but it can't
    add a custom header without a CORS preflight, which we never approve. So mutating
    requests must carry X-Requested-With, which only our own frontend sends.
    """
    if (
        request.method in MUTATING_METHODS
        and request.url.path.startswith("/api/")
        and "x-requested-with" not in request.headers
    ):
        return JSONResponse({"detail": "Missing X-Requested-With header."}, status_code=403)
    return await call_next(request)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app without opening resources. Tests attach their own pool, storage and
    resolver to app.state instead of running the lifespan."""
    app = FastAPI(title="tag-monitor", lifespan=lifespan)
    app.state.settings = settings or get_settings()
    app.middleware("http")(require_csrf_header)
    for module in (health, auth, sites, runs, alerts, ops):
        app.include_router(module.router)
    return app


app = create_app()
