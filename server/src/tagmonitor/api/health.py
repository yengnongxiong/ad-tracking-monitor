"""GET /api/health: used by Docker healthchecks and uptime monitors."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from tagmonitor.api.deps import get_pool
from tagmonitor.db.pool import Pool

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/health")
async def health(pool: Annotated[Pool, Depends(get_pool)]) -> JSONResponse:
    """Healthy means we can run a query, not just that the process is up."""
    try:
        async with pool.connection(timeout=2) as conn:
            await conn.execute("SELECT 1")
    except Exception:
        log.exception("health check could not reach the database")
        return JSONResponse({"status": "error", "database": "unreachable"}, status_code=503)
    return JSONResponse({"status": "ok", "database": "ok"})
