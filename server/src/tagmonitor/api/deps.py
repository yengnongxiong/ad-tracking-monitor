"""FastAPI dependencies shared by the routers."""

from typing import cast

from fastapi import Request

from tagmonitor.db.pool import Pool


def get_pool(request: Request) -> Pool:
    """The pool lives on app.state so tests can inject their own without running the lifespan."""
    return cast(Pool, request.app.state.pool)
