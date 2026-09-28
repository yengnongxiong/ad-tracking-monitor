"""Read what landed in Mailpit (the dev SMTP catcher) through its HTTP API."""

import os
from typing import Any

import httpx

MAILPIT_URL = os.environ.get("MAILPIT_URL", "http://localhost:8025")


async def messages_to(address: str) -> list[dict[str, Any]]:
    """Newest first. Tests use a unique recipient each, so they never see each other's mail."""
    async with httpx.AsyncClient(base_url=MAILPIT_URL) as client:
        response = await client.get("/api/v1/search", params={"query": f'to:"{address}"'})
        response.raise_for_status()
        messages: list[dict[str, Any]] = response.json()["messages"]
        return messages


async def message(message_id: str) -> dict[str, Any]:
    async with httpx.AsyncClient(base_url=MAILPIT_URL) as client:
        response = await client.get(f"/api/v1/message/{message_id}")
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        return body
