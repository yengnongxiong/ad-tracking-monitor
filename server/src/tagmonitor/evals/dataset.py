"""The message-match eval dataset: file formats and loading (PRD §15).

evals/message_match/dataset/ holds four files:

- examples.csv  (you write it) url, ad_headline, ad_primary_text, ad_cta: one row per ad. The
                same page can appear with several ads, e.g. one that fits and one that doesn't.
- pages.jsonl   (`collect` writes it) each page's text, frozen at collection time so the
                dataset stays reproducible when the live page changes.
- labels.jsonl  (`label` writes it, from your answers) your 1-5 overall score and notes.
- split.json    (`split` writes it) which examples are dev and which are test.

An example's id is a hash of its URL and ad copy, and each label records fingerprints of the
ad and page text it was given for. Editing an ad or re-collecting a changed page therefore
never silently reuses a label for text the labeler didn't see.
"""

import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from tagmonitor.checks.message_match import AdCopy, PageText
from tagmonitor.urls import normalize_url

# server/src/tagmonitor/evals/dataset.py -> the repo root.
EVAL_DIR = Path(__file__).resolve().parents[4] / "evals" / "message_match"
DATASET_DIR = EVAL_DIR / "dataset"
RESULTS_DIR = EVAL_DIR / "results"
EXAMPLES_FILE = "examples.csv"
PAGES_FILE = "pages.jsonl"
LABELS_FILE = "labels.jsonl"
SPLIT_FILE = "split.json"
EXAMPLE_COLUMNS = ("url", "ad_headline", "ad_primary_text", "ad_cta")

type SplitName = Literal["dev", "test"]


def _short_hash(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:12]


def page_id(url: str) -> str:
    return _short_hash(normalize_url(url))


def fingerprint(model: BaseModel) -> str:
    return hashlib.sha256(model.model_dump_json().encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Example:
    example_id: str
    url: str
    ad: AdCopy

    @property
    def page_id(self) -> str:
        return page_id(self.url)


def load_examples(path: Path) -> list[Example]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = set(EXAMPLE_COLUMNS) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path} is missing column(s): {', '.join(sorted(missing))}")
        rows = list(reader)
    examples: dict[str, Example] = {}
    for line, row in enumerate(rows, start=2):
        url = (row["url"] or "").strip()
        if not url:
            continue
        ad = AdCopy(
            headline=(row["ad_headline"] or "").strip() or None,
            primary_text=(row["ad_primary_text"] or "").strip() or None,
            cta=(row["ad_cta"] or "").strip() or None,
        )
        if ad.is_empty:
            raise ValueError(f"{path}:{line}: the row has a URL but no ad copy")
        example_id = _short_hash(normalize_url(url), ad.model_dump_json())
        if example_id in examples:
            raise ValueError(f"{path}:{line}: duplicate of an earlier row")
        examples[example_id] = Example(example_id, url, ad)
    return list(examples.values())


class FrozenPage(BaseModel):
    page_id: str
    url: str
    final_url: str | None
    http_status: int | None
    collected_at: datetime
    text: PageText


class Label(BaseModel):
    example_id: str
    overall: Annotated[int, Field(ge=1, le=5)]
    notes: str = ""
    labeler: str
    labeled_at: datetime
    # What the labeler was shown; a label only counts while both still match.
    ad_fingerprint: str
    page_fingerprint: str


class Split(BaseModel):
    seed: str
    test_fraction: float
    created_at: datetime
    dev: list[str]
    test: list[str]

    def ids(self, name: SplitName) -> set[str]:
        return set(self.dev if name == "dev" else self.test)


def load_pages(path: Path) -> dict[str, FrozenPage]:
    """By page id; a page collected again replaces the earlier copy (the last line wins)."""
    pages = {}
    for page in _read_jsonl(path, FrozenPage):
        pages[page.page_id] = page
    return pages


def load_labels(path: Path) -> dict[str, Label]:
    """By example id; relabeling an example appends a line, and the last one wins."""
    labels = {}
    for label in _read_jsonl(path, Label):
        labels[label.example_id] = label
    return labels


def load_split(path: Path) -> Split:
    return Split.model_validate_json(path.read_text())


def append_jsonl(path: Path, record: BaseModel) -> None:
    """Append one record and flush, so an interrupted session loses nothing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(record.model_dump_json() + "\n")
        handle.flush()


def _read_jsonl[M: BaseModel](path: Path, model: type[M]) -> list[M]:
    if not path.exists():
        return []
    records = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip():
            try:
                records.append(model.model_validate(json.loads(line)))
            except ValueError as exc:
                raise ValueError(f"{path}:{number}: {exc}") from exc
    return records


@dataclass(frozen=True)
class LabeledExample:
    example: Example
    page: FrozenPage
    label: Label


def labeled_examples(
    examples: list[Example], pages: dict[str, FrozenPage], labels: dict[str, Label]
) -> tuple[list[LabeledExample], list[str]]:
    """Examples with a page and a current label, plus the ids of stale labels (the ad or
    the page text changed after labeling)."""
    usable, stale = [], []
    for example in examples:
        page, label = pages.get(example.page_id), labels.get(example.example_id)
        if page is None or label is None:
            continue
        if (label.ad_fingerprint, label.page_fingerprint) != (
            fingerprint(example.ad),
            fingerprint(page.text),
        ):
            stale.append(example.example_id)
            continue
        usable.append(LabeledExample(example, page, label))
    return usable, stale
