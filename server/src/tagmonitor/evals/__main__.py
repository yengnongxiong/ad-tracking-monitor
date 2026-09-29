"""Message-match eval CLIs (PRD §15). See evals/message_match/README.md for the workflow.

python -m tagmonitor.evals status
python -m tagmonitor.evals collect             # pages for every URL in dataset/examples.csv
python -m tagmonitor.evals label               # you score each example 1-5
python -m tagmonitor.evals split
python -m tagmonitor.evals run --prompt v1 --split dev
python -m tagmonitor.evals report
"""

import asyncio
import getpass
from collections import Counter
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from tagmonitor.browser.capturer import PageCapturer
from tagmonitor.config import get_settings
from tagmonitor.db.pool import create_pool
from tagmonitor.evals.collect import collect as collect_pages
from tagmonitor.evals.collect import read_urls
from tagmonitor.evals.dataset import (
    DATASET_DIR,
    EXAMPLES_FILE,
    LABELS_FILE,
    PAGES_FILE,
    RESULTS_DIR,
    SPLIT_FILE,
    Example,
    SplitName,
    labeled_examples,
    load_examples,
    load_labels,
    load_pages,
    load_split,
)
from tagmonitor.evals.label import label_session
from tagmonitor.evals.report import write_report
from tagmonitor.evals.run import check_test_rule, save_result, score_examples, summarize
from tagmonitor.evals.split import DEFAULT_SEED, DEFAULT_TEST_FRACTION, make_split
from tagmonitor.llm.message_match import PURPOSE_EVAL, MessageMatcher
from tagmonitor.llm.pricing import price_for
from tagmonitor.llm.prompts import PromptError, load_prompt
from tagmonitor.llm.transport import AnthropicTransport

app = typer.Typer(add_completion=False, no_args_is_help=True)
console = Console()

DatasetDir = Annotated[Path, typer.Option(help="The eval dataset folder")]
ResultsDir = Annotated[Path, typer.Option(help="Where results files and report.md go")]


def _examples(dataset_dir: Path) -> list[Example]:
    path = dataset_dir / EXAMPLES_FILE
    if not path.exists():
        raise typer.BadParameter(
            f"{path} doesn't exist yet. Write your ads there first "
            "(the format is in evals/message_match/README.md)."
        )
    return load_examples(path)


@app.command()
def status(dataset_dir: DatasetDir = DATASET_DIR) -> None:
    """How far along the dataset is: examples, collected pages, labels, split."""
    examples = _examples(dataset_dir)
    pages = load_pages(dataset_dir / PAGES_FILE)
    labels = load_labels(dataset_dir / LABELS_FILE)
    usable, stale = labeled_examples(examples, pages, labels)
    table = Table("", "Count", title="Message match eval dataset")
    table.add_row("Examples (ads)", str(len(examples)))
    table.add_row("Distinct pages", str(len({e.page_id for e in examples})))
    table.add_row("Pages collected", str(len({e.page_id for e in examples} & set(pages))))
    table.add_row("Labeled", str(len(usable)))
    table.add_row("Stale labels (page changed)", str(len(stale)))
    console.print(table)
    split_path = dataset_dir / SPLIT_FILE
    if split_path.exists():
        split = load_split(split_path)
        names: tuple[SplitName, ...] = ("dev", "test")
        for name in names:
            ids = split.ids(name)
            scores = Counter(
                item.label.overall for item in usable if item.example.example_id in ids
            )
            spread = ", ".join(f"{score}: {scores[score]}" for score in range(1, 6))
            console.print(f"{name}: {len(ids)} examples, labels {spread}")


@app.command()
def collect(
    urls: Annotated[
        Path | None, typer.Option(help="URL list (.txt) or CSV with a url column")
    ] = None,
    dataset_dir: DatasetDir = DATASET_DIR,
    refresh: Annotated[bool, typer.Option(help="Re-collect pages already collected")] = False,
) -> None:
    """Load each page once on mobile and freeze its text into pages.jsonl."""
    settings = get_settings()
    url_list = read_urls(urls or dataset_dir / EXAMPLES_FILE)

    async def go() -> None:
        async with PageCapturer(policy=settings.ssrf_policy()) as capturer:
            summary = await collect_pages(
                url_list, dataset_dir / PAGES_FILE, capturer, refresh=refresh
            )
        console.print(
            f"collected {len(summary.collected)}, already had {len(summary.already_had)}, "
            f"failed {len(summary.failed)}"
        )
        for url, why in summary.failed.items():
            console.print(f"[red]failed[/red] {url}: {why}")

    asyncio.run(go())


@app.command()
def label(
    dataset_dir: DatasetDir = DATASET_DIR,
    labeler: Annotated[str, typer.Option(help="Your name, stored with each label")] = "",
    relabel: Annotated[
        list[str] | None, typer.Option(help="An example id to label again (repeatable)")
    ] = None,
) -> None:
    """Score each example yourself, 1-5. Resumable; s skips, q quits."""
    summary = label_session(
        _examples(dataset_dir),
        load_pages(dataset_dir / PAGES_FILE),
        dataset_dir / LABELS_FILE,
        labeler=labeler or getpass.getuser(),
        console=console,
        relabel=frozenset(relabel or []),
    )
    console.print(
        f"\nlabeled {summary.labeled}, skipped {summary.skipped}, left for later "
        f"{summary.remaining}"
    )


@app.command()
def split(
    dataset_dir: DatasetDir = DATASET_DIR,
    seed: str = DEFAULT_SEED,
    test_fraction: float = DEFAULT_TEST_FRACTION,
    force: Annotated[bool, typer.Option(help="Allow a different seed or fraction")] = False,
) -> None:
    """Deterministic dev/test split, by page."""
    result = make_split(
        _examples(dataset_dir),
        dataset_dir / SPLIT_FILE,
        seed=seed,
        test_fraction=test_fraction,
        force=force,
    )
    console.print(f"dev: {len(result.dev)} examples, test: {len(result.test)} examples")


@app.command()
def run(
    prompt: Annotated[str, typer.Option(help="Prompt version, e.g. v2")],
    split_name: Annotated[str, typer.Option("--split", help="dev or test")],
    concurrency: Annotated[int, typer.Option(min=1, max=16)] = 4,
    rerun_test: Annotated[bool, typer.Option(help="Score the test split again")] = False,
    price_in: Annotated[float | None, typer.Option(help="USD per million input tokens")] = None,
    price_out: Annotated[float | None, typer.Option(help="USD per million output tokens")] = None,
    dataset_dir: DatasetDir = DATASET_DIR,
    results_dir: ResultsDir = RESULTS_DIR,
) -> None:
    """Score a split with a prompt version; write a results file and update report.md."""
    if split_name not in ("dev", "test"):
        raise typer.BadParameter("--split must be dev or test")
    split_as: SplitName = "dev" if split_name == "dev" else "test"
    check_test_rule(results_dir, split_as, rerun_test=rerun_test)
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise typer.BadParameter("set ANTHROPIC_API_KEY (in .env) to run evals")

    examples = _examples(dataset_dir)
    usable, stale = labeled_examples(
        examples, load_pages(dataset_dir / PAGES_FILE), load_labels(dataset_dir / LABELS_FILE)
    )
    if not (dataset_dir / SPLIT_FILE).exists():
        raise typer.BadParameter("no split yet: run `split` first")
    ids = load_split(dataset_dir / SPLIT_FILE).ids(split_as)
    items = [item for item in usable if item.example.example_id in ids]
    if not items:
        raise typer.BadParameter(f"no labeled examples in the {split_as} split")
    price = (price_in, price_out) if price_in is not None and price_out is not None else None
    try:
        load_prompt(prompt)
    except PromptError as exc:
        raise typer.BadParameter(str(exc)) from exc
    transport = AnthropicTransport(
        settings.anthropic_api_key,
        timeout_s=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )

    async def go() -> None:
        try:
            async with create_pool(settings.database_url, max_size=concurrency + 2) as pool:
                matcher = MessageMatcher.from_settings(
                    pool, transport, settings, prompt_version=prompt, purpose=PURPOSE_EVAL
                )
                console.print(f"scoring {len(items)} {split_as} examples with prompt {prompt}")
                scored = await score_examples(items, matcher, concurrency=concurrency)
                result = summarize(
                    scored,
                    matcher=matcher,
                    split=split_as,
                    stale_labels=sum(1 for s in stale if s in ids),
                    price=price or price_for(matcher.config.model),
                )
        finally:
            await transport.aclose()
        path = save_result(result, results_dir)
        write_report(results_dir)
        metrics = result.metrics or {}
        exact, kappa = metrics.get("exact", float("nan")), metrics.get("kappa", float("nan"))
        console.print(
            f"wrote {path.name}: n={result.n_scored}, exact {exact:.0%}, QWK {kappa:.2f}, "
            f"errors {result.n_errors}"
        )

    asyncio.run(go())


@app.command()
def report(results_dir: ResultsDir = RESULTS_DIR) -> None:
    """Regenerate report.md from the results files."""
    console.print(f"wrote {write_report(results_dir)}")


if __name__ == "__main__":
    app()
