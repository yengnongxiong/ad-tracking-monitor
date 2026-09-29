"""Message match: does the landing page follow through on what the ad promised? (PRD §15)

A language model reads the ad copy and the page's text and returns a structured verdict.
tagmonitor.llm makes that call (cached, within a daily budget) before this check runs; this
module is the pure part. It defines what goes in (AdCopy, PageText), what comes out
(MessageMatchVerdict), and maps a verdict, or the reason there isn't one, to a status.
Same inputs, same result, no I/O (ADR-017).
"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from tagmonitor.checks.base import CheckBase, CheckResult
from tagmonitor.page_capture import PageCapture

type LlmErrorCode = Literal[
    "llm_not_configured", "llm_daily_limit", "llm_error", "llm_invalid_output"
]


class AdCopy(BaseModel):
    """The ad as the site owner wrote it (sites.ad_headline, ad_primary_text, ad_cta)."""

    headline: str | None = None
    primary_text: str | None = None
    cta: str | None = None

    @property
    def is_empty(self) -> bool:
        return not any(
            (value or "").strip() for value in (self.headline, self.primary_text, self.cta)
        )


class PageText(BaseModel):
    """The page's words as a visitor first meets them, taken from the capture."""

    title: str | None = None
    meta_description: str | None = None
    h1: list[str] = Field(default_factory=list)
    above_fold_text: str = ""
    button_texts: list[str] = Field(default_factory=list)

    @classmethod
    def from_capture(cls, capture: PageCapture) -> "PageText | None":
        """None when there's no page to read (it didn't load)."""
        dom = capture.dom
        if dom is None or not capture.navigation.document_loaded:
            return None
        return cls(
            title=dom.title,
            meta_description=dom.meta_description,
            h1=dom.h1,
            above_fold_text=dom.above_fold_text,
            button_texts=dom.button_texts,
        )


Score = Annotated[int, Field(ge=1, le=5)]
Note = Annotated[str, Field(max_length=500)]


class MessageMatchVerdict(BaseModel):
    """The model's answer. The field order is the order the model writes them in: what it
    noticed first, then the scores, so the scores follow from the observations."""

    issues: list[Note] = Field(
        max_length=6,
        description="Specific mismatches between the ad and the page, in plain English for a "
        "small-business owner. Empty if there are none.",
    )
    offer_consistency: Score = Field(
        description="Does the page carry the ad's offer: the same product or service, price, "
        "discount and terms? 1 = different or missing, 5 = identical and prominent."
    )
    headline_relevance: Score = Field(
        description="Does the page's headline pick up the ad's promise? 1 = unrelated, "
        "5 = clearly continues it."
    )
    cta_alignment: Score = Field(
        description="Does the page's main button or action do what the ad's call to action "
        "says? 1 = no such action, 5 = the same action, easy to find."
    )
    overall: Score = Field(
        description="How well a visitor who clicked the ad finds what it promised, using the "
        "rubric. 1 = unrelated or contradictory, 5 = seamless."
    )
    suggestions: list[Note] = Field(
        max_length=6,
        description="Concrete changes to the page (or the ad) that would improve the match. "
        "Empty if none are needed.",
    )


class MessageMatchOutcome(BaseModel):
    """What the worker hands the check: a verdict, or why there isn't one. Empty when the
    page didn't load, so there was nothing to ask the model about."""

    verdict: MessageMatchVerdict | None = None
    error: LlmErrorCode | None = None
    error_detail: str = ""
    model: str = ""
    prompt_version: str = ""
    cached: bool = False
    # What the verdict cost (for a cache hit: what it cost when it was made). Eval reports
    # use these; they aren't stored with the check result.
    input_tokens: int = 0
    output_tokens: int = 0


_ERRORS: dict[LlmErrorCode, tuple[Literal["info", "error"], str]] = {
    "llm_not_configured": (
        "info",
        "Message match is turned off on this server (no Anthropic API key is set).",
    ),
    "llm_daily_limit": ("error", "Not checked: this server reached its daily limit of AI checks."),
    "llm_error": ("error", "Not checked: the AI service didn't answer."),
    "llm_invalid_output": ("error", "Not checked: the AI's answer wasn't usable."),
}


class MessageMatchCheck(CheckBase):
    check_key = "message_match"
    title = "Message match"
    # One model call per check run, on the device most ad clicks come from.
    devices = frozenset({"mobile"})
    codes = frozenset({"good_match", "partial_match", "poor_match", *_ERRORS})

    def analyze(self, capture: PageCapture, outcome: MessageMatchOutcome) -> CheckResult:
        if PageText.from_capture(capture) is None:
            return self.not_evaluated(capture)  # nothing was sent to the model
        if outcome.verdict is None:
            status, summary = _ERRORS[outcome.error or "llm_error"]
            return self.result(
                status, outcome.error or "llm_error", summary, detail=outcome.error_detail
            )

        verdict = outcome.verdict
        details = verdict.model_dump() | {
            "model": outcome.model,
            "prompt_version": outcome.prompt_version,
            "cached": outcome.cached,
        }
        score = f"{verdict.overall}/5"
        if verdict.overall <= 2:
            summary = f"Your ad and this page tell different stories (match {score})."
            return self.result("fail", "poor_match", summary, **details)
        if verdict.overall == 3:
            summary = f"This page only partly follows through on your ad (match {score})."
            return self.result("warn", "partial_match", summary, **details)
        summary = f"This page follows through on your ad (match {score})."
        return self.result("pass", "good_match", summary, **details)
