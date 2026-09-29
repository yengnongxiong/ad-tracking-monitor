"""`label`: the interactive labeling session. You read the ad and the page and give the overall
1-5 score; this only records your answers (CLAUDE.md: labels are the human's, never ours).

- Blind: the model's scores are never shown, so they can't anchor you.
- Resumable: every answer is appended and flushed at once; the next session starts where you
  stopped. `s` skips an example, `q` quits.
- Shuffled deterministically, so the ads written for the same page don't come back to back.
"""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console, Group
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

from tagmonitor.evals.dataset import (
    Example,
    FrozenPage,
    Label,
    append_jsonl,
    fingerprint,
    load_labels,
)

RUBRIC = (
    "5 seamless: the offer and the action the ad promised, right away\n"
    "4 clearly the right page, small gaps (a detail below the fold, a differently worded button)\n"
    "3 related, but the visitor has to work to connect it to the ad\n"
    "2 mostly a mismatch: a generic page, or the promised offer isn't visible\n"
    "1 unrelated or contradictory (other offer, conflicting price, error or empty page)"
)


@dataclass
class LabelSummary:
    labeled: int = 0
    skipped: int = 0
    remaining: int = 0
    missing_page: int = 0


def _shuffle_key(example: Example) -> str:
    return hashlib.sha256(f"label-order:{example.example_id}".encode()).hexdigest()


def label_session(
    examples: list[Example],
    pages: dict[str, FrozenPage],
    labels_path: Path,
    *,
    labeler: str,
    console: Console,
    relabel: frozenset[str] = frozenset(),
) -> LabelSummary:
    """`relabel`: example ids to ask about again even though they have a current label (the
    new answer is appended, and the latest label counts)."""
    labels = load_labels(labels_path)
    summary = LabelSummary()
    todo: list[tuple[Example, FrozenPage, bool]] = []
    for example in sorted(examples, key=_shuffle_key):
        page = pages.get(example.page_id)
        if page is None:
            summary.missing_page += 1
            continue
        label = labels.get(example.example_id)
        current = label is not None and (label.ad_fingerprint, label.page_fingerprint) == (
            fingerprint(example.ad),
            fingerprint(page.text),
        )
        if not current or example.example_id in relabel:
            todo.append((example, page, label is not None and not current))
    if summary.missing_page:
        console.print(
            f"[yellow]{summary.missing_page} example(s) have no collected page yet; "
            "run `collect` first.[/yellow]"
        )
    done_before = len(examples) - summary.missing_page - len(todo)
    console.print(f"{done_before} labeled, {len(todo)} to go. Enter s to skip, q to quit.\n")

    for position, (example, page, changed) in enumerate(todo):
        console.rule(f"Example {position + 1} of {len(todo)}  ({example.example_id})")
        console.print(_ad_panel(example))
        console.print(_page_panel(page, changed))
        console.print(Panel(RUBRIC, title="Overall match", border_style="dim"))
        answer = Prompt.ask(
            "Overall match (1-5)", choices=["1", "2", "3", "4", "5", "s", "q"], console=console
        )
        if answer == "q":
            summary.remaining = len(todo) - position
            break
        if answer == "s":
            summary.skipped += 1
            continue
        notes = Prompt.ask("Notes (optional)", default="", show_default=False, console=console)
        append_jsonl(
            labels_path,
            Label(
                example_id=example.example_id,
                overall=int(answer),
                notes=notes.strip(),
                labeler=labeler,
                labeled_at=datetime.now(UTC),
                ad_fingerprint=fingerprint(example.ad),
                page_fingerprint=fingerprint(page.text),
            ),
        )
        summary.labeled += 1
    return summary


def _field_table(rows: list[tuple[str, str]]) -> Table:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold", no_wrap=True)
    table.add_column()
    for name, value in rows:
        table.add_row(name, Text(value or "(none)"))  # Text: page content is never markup
    return table


def _ad_panel(example: Example) -> Panel:
    ad = example.ad
    rows = [
        ("Headline", ad.headline or ""),
        ("Primary text", ad.primary_text or ""),
        ("Call to action", ad.cta or ""),
    ]
    return Panel(_field_table(rows), title="The ad", border_style="cyan")


def _page_panel(page: FrozenPage, changed: bool) -> Panel:
    text = page.text
    rows = [
        ("URL", page.url),
        ("Title", text.title or ""),
        ("Description", text.meta_description or ""),
        ("Headings", "\n".join(text.h1)),
        ("Visible text", text.above_fold_text),
        ("Buttons, links", " | ".join(text.button_texts)),
    ]
    body = _field_table(rows)
    title = "The landing page: what a phone shows before scrolling"
    if changed:
        note = Text("The page's text changed since you labeled it, so it needs a new label.")
        return Panel(Group(note, body), title=title, border_style="yellow")
    return Panel(body, title=title, border_style="green")
