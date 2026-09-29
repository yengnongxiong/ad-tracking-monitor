"""Ask the model whether a landing page matches its ad: cached, budgeted, validated (PRD §15).

assess() order of business:
1. No API key -> "llm_not_configured" (no call).
2. Cache hit on sha256(model, prompt version and text, ad copy, page text) -> reuse it. Pages
   rarely change between checks, so most scheduled checks cost nothing.
3. Reserve a call against LLM_MAX_CALLS_PER_DAY (a row in llm_usage, under an advisory lock
   so concurrent workers can't overshoot), then call the API.
4. Validate the answer with Pydantic. If it doesn't validate, send the validation error back
   once and ask again. Only a valid verdict is cached.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from tagmonitor.checks.message_match import (
    AdCopy,
    LlmErrorCode,
    MessageMatchOutcome,
    MessageMatchVerdict,
    PageText,
)
from tagmonitor.config import Settings
from tagmonitor.db.pool import Pool
from tagmonitor.llm.prompts import Prompt, load_prompt
from tagmonitor.llm.transport import LlmCallError, LlmTransport

TOOL_NAME = "record_message_match"
TOOL = {
    "name": TOOL_NAME,
    "description": "Record your assessment of how well the landing page matches the ad.",
    "input_schema": MessageMatchVerdict.model_json_schema(),
}
# Advisory lock keys (7311, n) are this app's; 1 and 2 are the scheduler and the reaper.
BUDGET_LOCK = (7311, 3)
PURPOSE_MONITORING = "message_match"
PURPOSE_EVAL = "eval"


@dataclass(frozen=True)
class LlmConfig:
    model: str
    max_calls_per_day: int
    temperature: float | None
    max_tokens: int

    @classmethod
    def from_settings(cls, settings: Settings) -> "LlmConfig":
        return cls(
            model=settings.llm_model,
            max_calls_per_day=settings.llm_max_calls_per_day,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
        )


class MessageMatcher:
    def __init__(
        self,
        pool: Pool,
        transport: LlmTransport | None,
        prompt: Prompt,
        config: LlmConfig,
        *,
        purpose: str = PURPOSE_MONITORING,
    ) -> None:
        self.pool = pool
        self.transport = transport
        self.prompt = prompt
        self.config = config
        self.purpose = purpose

    @classmethod
    def from_settings(
        cls,
        pool: Pool,
        transport: LlmTransport | None,
        settings: Settings,
        *,
        prompt_version: str | None = None,
        purpose: str = PURPOSE_MONITORING,
    ) -> "MessageMatcher":
        prompt = load_prompt(prompt_version or settings.llm_prompt_version)
        return cls(pool, transport, prompt, LlmConfig.from_settings(settings), purpose=purpose)

    def cache_key(self, ad: AdCopy, page: PageText) -> str:
        material = {
            "model": self.config.model,
            "prompt_version": self.prompt.version,
            "prompt_sha256": self.prompt.sha256,
            "ad": ad.model_dump(),
            "page": page.model_dump(),
        }
        return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()

    def request(self, ad: AdCopy, page: PageText) -> dict[str, Any]:
        request: dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,
            "system": self.prompt.system,
            "messages": [{"role": "user", "content": self.prompt.render_user(ad, page)}],
            "tools": [TOOL],
            "tool_choice": {"type": "tool", "name": TOOL_NAME},
        }
        if self.config.temperature is not None:
            # No longer a typed SDK parameter (deprecated in the API), so it goes in the body.
            request["extra_body"] = {"temperature": self.config.temperature}
        return request

    async def assess(self, ad: AdCopy, page: PageText) -> MessageMatchOutcome:
        if self.transport is None:
            return self._outcome(error="llm_not_configured")
        key = self.cache_key(ad, page)
        if (cached := await self._cache_get(key)) is not None:
            return cached

        request = self.request(ad, page)
        input_tokens = output_tokens = 0
        for attempt in (1, 2):
            usage_id = await self._reserve_call()
            if usage_id is None:
                return self._outcome(
                    error="llm_daily_limit",
                    detail=f"{self.config.max_calls_per_day} calls per day",
                )
            try:
                response = await self.transport.create(request)
            except LlmCallError as exc:
                return self._outcome(error="llm_error", detail=str(exc))
            usage = response.get("usage") or {}
            input_tokens += int(usage.get("input_tokens") or 0)
            output_tokens += int(usage.get("output_tokens") or 0)
            await self._record_usage(usage_id, usage)

            tool_use = next(
                (
                    block
                    for block in response.get("content", [])
                    if block.get("type") == "tool_use" and block.get("name") == TOOL_NAME
                ),
                None,
            )
            try:
                if tool_use is None:
                    raise ValueError("the answer didn't use the record_message_match tool")
                verdict = MessageMatchVerdict.model_validate(tool_use.get("input"))
            except (ValidationError, ValueError) as exc:
                if attempt == 2:
                    return self._outcome(error="llm_invalid_output", detail=str(exc)[:1000])
                if tool_use is not None:
                    request = _with_correction(request, response, tool_use, exc)
                continue  # without a tool call to correct, just ask again
            await self._cache_put(key, verdict, input_tokens, output_tokens)
            return self._outcome(
                verdict=verdict, input_tokens=input_tokens, output_tokens=output_tokens
            )
        raise AssertionError("unreachable")  # the loop always returns

    # -- helpers ------------------------------------------------------------------------------

    def _outcome(
        self,
        *,
        verdict: MessageMatchVerdict | None = None,
        error: LlmErrorCode | None = None,
        detail: str = "",
        cached: bool = False,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> MessageMatchOutcome:
        return MessageMatchOutcome(
            verdict=verdict,
            error=error,
            error_detail=detail,
            model=self.config.model,
            prompt_version=self.prompt.version,
            cached=cached,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    async def _cache_get(self, key: str) -> MessageMatchOutcome | None:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT response FROM llm_cache WHERE cache_key = %s", (key,)
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        response = row["response"]
        return self._outcome(
            verdict=MessageMatchVerdict.model_validate(response["verdict"]),
            cached=True,
            input_tokens=int(response.get("input_tokens", 0)),
            output_tokens=int(response.get("output_tokens", 0)),
        )

    async def _cache_put(
        self, key: str, verdict: MessageMatchVerdict, input_tokens: int, output_tokens: int
    ) -> None:
        response = {
            "verdict": verdict.model_dump(),
            # What the answer cost when it was made, so eval reports can show full cost.
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO llm_cache (cache_key, model, prompt_version, response) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (cache_key) DO NOTHING",
                (key, self.config.model, self.prompt.version, json.dumps(response)),
            )

    async def _reserve_call(self) -> int | None:
        """Count today's calls and claim one, atomically across all workers. Every attempt
        counts, including ones that then fail: the cap bounds what we could be billed."""
        async with self.pool.connection() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(%s, %s)", BUDGET_LOCK)
            cursor = await conn.execute(
                # The UTC day, whatever the session's time zone.
                "SELECT count(*) AS calls FROM llm_usage "
                "WHERE created_at >= date_trunc('day', now(), 'UTC')"
            )
            row = await cursor.fetchone()
            if row is None or row["calls"] >= self.config.max_calls_per_day:
                return None
            cursor = await conn.execute(
                "INSERT INTO llm_usage (model, purpose, input_tokens, output_tokens) "
                "VALUES (%s, %s, 0, 0) RETURNING id",
                (self.config.model, self.purpose),
            )
            reserved = await cursor.fetchone()
        assert reserved is not None
        return int(reserved["id"])

    async def _record_usage(self, usage_id: int, usage: dict[str, Any]) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "UPDATE llm_usage SET input_tokens = %s, output_tokens = %s WHERE id = %s",
                (
                    int(usage.get("input_tokens") or 0),
                    int(usage.get("output_tokens") or 0),
                    usage_id,
                ),
            )


def _with_correction(
    request: dict[str, Any],
    response: dict[str, Any],
    tool_use: dict[str, Any],
    error: Exception,
) -> dict[str, Any]:
    """The follow-up request for the one retry: the model's answer, then the validation
    error as the tool's result, so it can fix exactly what was wrong."""
    return request | {
        "messages": [
            *request["messages"],
            {"role": "assistant", "content": _as_params(response["content"])},
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use["id"],
                        "is_error": True,
                        "content": f"That answer didn't match the schema: {error}. "
                        f"Call {TOOL_NAME} again with a corrected answer.",
                    }
                ],
            },
        ]
    }


def _as_params(content: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Response content blocks as request content: only the fields a request may carry."""
    blocks: list[dict[str, Any]] = []
    for block in content:
        if block.get("type") == "text":
            blocks.append({"type": "text", "text": block["text"]})
        elif block.get("type") == "tool_use":
            blocks.append({k: block[k] for k in ("type", "id", "name", "input")})
    return blocks
