import httpx

from tagmonitor.api.app import create_app
from tagmonitor.db.pool import Pool, create_pool


def client_for(pool: Pool) -> httpx.AsyncClient:
    app = create_app()
    app.state.pool = pool
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_health_ok_when_database_reachable(pool: Pool) -> None:
    async with client_for(pool) as client:
        response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


async def test_health_503_when_database_unreachable() -> None:
    # Port 1 on localhost refuses connections, so the pool can never hand out a connection.
    unreachable = create_pool("postgresql://nobody@127.0.0.1:1/none", min_size=0)
    await unreachable.open(wait=False)
    try:
        async with client_for(unreachable) as client:
            response = await client.get("/api/health")
    finally:
        await unreachable.close()
    assert response.status_code == 503
    assert response.json()["database"] == "unreachable"
