"""The one place that talks to the Anthropic API.

Everything above this layer builds plain request dicts and reads plain response dicts, so
tests swap in a fake transport and never touch the network (CLAUDE.md).
"""

from typing import Any, Protocol

import anthropic
import httpx2  # the SDK's HTTP client (a fork of httpx)


class LlmCallError(Exception):
    """The API call failed after the SDK's own retries (or failed in a way retries can't fix)."""


class LlmTransport(Protocol):
    async def create(self, request: dict[str, Any]) -> dict[str, Any]:
        """Send a Messages API request; return the response as JSON-compatible data."""
        ...


class AnthropicTransport:
    def __init__(
        self,
        api_key: str,
        *,
        timeout_s: float,
        max_retries: int,
        http_client: httpx2.AsyncClient | None = None,  # tests pass one with a mock transport
    ) -> None:
        # The SDK retries connection errors, 408, 409, 429 and 5xx with exponential backoff,
        # honoring Retry-After (PRD §15: retries on 429/5xx).
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key, timeout=timeout_s, max_retries=max_retries, http_client=http_client
        )

    async def create(self, request: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.messages.create(**request)
        except anthropic.APIError as exc:
            raise LlmCallError(f"{type(exc).__name__}: {exc}") from exc
        data: dict[str, Any] = response.model_dump(mode="json")
        return data

    async def aclose(self) -> None:
        await self._client.close()
