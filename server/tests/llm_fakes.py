"""A scripted stand-in for the Anthropic API: tests never call the real one."""

import asyncio
from collections.abc import Callable
from typing import Any

from tagmonitor.llm.message_match import TOOL_NAME
from tagmonitor.llm.transport import LlmCallError


def verdict(overall: int = 4, **overrides: Any) -> dict[str, Any]:
    """A valid tool input, as the model would send it."""
    return {
        "issues": [] if overall >= 4 else ["The page doesn't mention the ad's offer."],
        "offer_consistency": overall,
        "headline_relevance": overall,
        "cta_alignment": overall,
        "overall": overall,
        "suggestions": [] if overall >= 4 else ["Repeat the ad's offer in the headline."],
    } | overrides


def tool_response(
    tool_input: dict[str, Any] | None, *, input_tokens: int = 900, output_tokens: int = 120
) -> dict[str, Any]:
    """A Messages API response carrying one tool call (or, with None, only text)."""
    content: list[dict[str, Any]]
    if tool_input is None:
        content = [{"type": "text", "text": "I think it's a 4.", "citations": None}]
    else:
        content = [{"type": "tool_use", "id": "toolu_01", "name": TOOL_NAME, "input": tool_input}]
    return {
        "id": "msg_01",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-4-5-20251001",
        "content": content,
        "stop_reason": "tool_use",
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


Responder = Callable[[dict[str, Any]], dict[str, Any]]


class FakeTransport:
    """Answers each request with the next scripted response, or with a function of the
    request. Records every request it gets."""

    def __init__(
        self,
        responses: list[dict[str, Any] | Exception] | None = None,
        *,
        responder: Responder | None = None,
        delay_s: float = 0.0,
    ) -> None:
        self.responses = list(responses or [])
        self.responder = responder
        self.delay_s = delay_s
        self.requests: list[dict[str, Any]] = []

    async def create(self, request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(request)
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.responder is not None:
            return self.responder(request)
        if not self.responses:
            raise AssertionError("FakeTransport got more requests than scripted responses")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def failing(message: str = "OverloadedError: overloaded") -> LlmCallError:
    return LlmCallError(message)
