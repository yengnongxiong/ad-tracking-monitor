"""The async Postgres connection pool shared by the API and the worker."""

from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

type Pool = AsyncConnectionPool[AsyncConnection[DictRow]]


def create_pool(database_url: str, *, min_size: int = 1, max_size: int = 10) -> Pool:
    """Build a pool whose connections return rows as dicts and run in autocommit mode.

    Autocommit means a borrowed connection never sits "idle in transaction" (which would hold
    locks and block vacuum), e.g. while a worker holds a per-domain advisory lock for a whole
    capture. Work that must be atomic says so explicitly with `async with conn.transaction()`.

    The pool is created closed (open=False) so the caller opens it explicitly at startup,
    where a bad DATABASE_URL fails loudly instead of on the first request.
    """
    return AsyncConnectionPool(
        database_url,
        min_size=min_size,
        max_size=max_size,
        open=False,
        connection_class=AsyncConnection[DictRow],
        kwargs={"row_factory": dict_row, "autocommit": True},
    )
