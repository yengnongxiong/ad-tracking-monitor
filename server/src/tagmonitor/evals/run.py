"""`run`: score one split with one prompt version and save the results (PRD §15).

The model is called through the same MessageMatcher as monitoring (cache, daily budget,
validation), with a concurrency limit. Results files are never overwritten; report.md
compares them.

Process rule, enforced here: prompts are tuned on dev, and the test split is scored once, at
the end. Scoring test again needs --rerun-test, and the report shows every test run.
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from tagmonitor.evals.dataset import LabeledExample, SplitName
from tagmonitor.evals.metrics import compute_metrics
from tagmonitor.llm.message_match import MessageMatcher
from tagmonitor.llm.pricing import cost_usd

SCHEMA_VERSION = 1


class ExampleResult(BaseModel):
    example_id: str
    page_id: str
    human: int
    model: int | None  # None when the model gave no usable answer
    error: str | None = None
    scores: dict[str, int] = {}
    issues: list[str] = []
    human_notes: str = ""
    cached: bool = False
    input_tokens: int = 0
    output_tokens: int = 0


class EvalResult(BaseModel):
    schema_version: int = SCHEMA_VERSION
    created_at: datetime
    prompt_version: str
    prompt_sha256: str
    model: str
    split: SplitName
    n_labeled: int
    n_scored: int
    n_errors: int
    stale_labels: int
    metrics: dict[str, object] | None
    input_tokens: int  # what all the answers cost, cached ones included
    output_tokens: int
    spent_input_tokens: int  # what this run actually spent (cache misses)
    spent_output_tokens: int
    estimated_cost_usd: float | None
    spent_cost_usd: float | None
    examples: list[ExampleResult]

    @property
    def filename(self) -> str:
        return f"{self.created_at:%Y%m%d-%H%M%S}-{self.prompt_version}-{self.split}.json"


class SplitRuleViolation(RuntimeError):
    pass


def check_test_rule(results_dir: Path, split: SplitName, *, rerun_test: bool) -> None:
    if split != "test" or rerun_test:
        return
    earlier = [r for r in load_results(results_dir) if r.split == "test"]
    if earlier:
        runs = ", ".join(f"{r.prompt_version} ({r.created_at:%Y-%m-%d})" for r in earlier)
        raise SplitRuleViolation(
            f"the test split was already scored: {runs}. Tune prompts on dev; score test "
            "once at the end. Pass --rerun-test if you really mean to (it shows in the report)."
        )


async def score_examples(
    items: list[LabeledExample], matcher: MessageMatcher, *, concurrency: int = 4
) -> list[ExampleResult]:
    semaphore = asyncio.Semaphore(concurrency)

    async def one(item: LabeledExample) -> ExampleResult:
        async with semaphore:
            outcome = await matcher.assess(item.example.ad, item.page.text)
        verdict = outcome.verdict
        return ExampleResult(
            example_id=item.example.example_id,
            page_id=item.example.page_id,
            human=item.label.overall,
            model=verdict.overall if verdict else None,
            error=None if verdict else f"{outcome.error}: {outcome.error_detail}",
            scores=(
                {
                    "offer_consistency": verdict.offer_consistency,
                    "headline_relevance": verdict.headline_relevance,
                    "cta_alignment": verdict.cta_alignment,
                }
                if verdict
                else {}
            ),
            issues=verdict.issues if verdict else [],
            human_notes=item.label.notes,
            cached=outcome.cached,
            input_tokens=outcome.input_tokens,
            output_tokens=outcome.output_tokens,
        )

    return list(await asyncio.gather(*(one(item) for item in items)))


def summarize(
    examples: list[ExampleResult],
    *,
    matcher: MessageMatcher,
    split: SplitName,
    stale_labels: int,
    price: tuple[float, float] | None,
) -> EvalResult:
    pairs = [(e.human, e.model) for e in examples if e.model is not None]
    metrics = compute_metrics([human for human, _ in pairs], [model for _, model in pairs])
    tokens_in = sum(e.input_tokens for e in examples)
    tokens_out = sum(e.output_tokens for e in examples)
    spent_in = sum(e.input_tokens for e in examples if not e.cached)
    spent_out = sum(e.output_tokens for e in examples if not e.cached)
    return EvalResult(
        created_at=datetime.now(UTC),
        prompt_version=matcher.prompt.version,
        prompt_sha256=matcher.prompt.sha256,
        model=matcher.config.model,
        split=split,
        n_labeled=len(examples),
        n_scored=len(pairs),
        n_errors=len(examples) - len(pairs),
        stale_labels=stale_labels,
        metrics=metrics.as_dict() if pairs else None,
        input_tokens=tokens_in,
        output_tokens=tokens_out,
        spent_input_tokens=spent_in,
        spent_output_tokens=spent_out,
        estimated_cost_usd=cost_usd(tokens_in, tokens_out, price) if price else None,
        spent_cost_usd=cost_usd(spent_in, spent_out, price) if price else None,
        examples=sorted(examples, key=lambda e: e.example_id),
    )


def save_result(result: EvalResult, results_dir: Path) -> Path:
    results_dir.mkdir(parents=True, exist_ok=True)
    path = results_dir / result.filename
    if path.exists():
        raise FileExistsError(path)  # results are a record: never overwrite one
    path.write_text(result.model_dump_json(indent=2) + "\n")
    return path


def load_results(results_dir: Path) -> list[EvalResult]:
    results = [
        EvalResult.model_validate_json(path.read_text())
        for path in sorted(results_dir.glob("*.json"))
    ]
    return sorted(results, key=lambda r: r.created_at)
