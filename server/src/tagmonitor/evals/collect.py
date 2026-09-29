"""`collect`: load each page once (mobile, same capturer and SSRF guard as monitoring) and
freeze its text into pages.jsonl, so labels and eval runs always see the same words."""

import csv
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from tagmonitor.browser.capturer import CaptureError, PageCapturer
from tagmonitor.browser.ssrf import SsrfError
from tagmonitor.checks.message_match import PageText
from tagmonitor.evals.dataset import FrozenPage, append_jsonl, load_pages, page_id


def read_urls(path: Path) -> list[str]:
    """One URL per line (.txt), or the url column of a CSV such as examples.csv."""
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle))
        urls = [(row.get("url") or "").strip() for row in rows]
    else:
        lines = path.read_text(encoding="utf-8").splitlines()
        urls = [line.strip() for line in lines if not line.lstrip().startswith("#")]
    unique: dict[str, str] = {}
    for url in urls:
        if url:
            unique.setdefault(page_id(url), url)
    return list(unique.values())


@dataclass
class CollectSummary:
    collected: list[str] = field(default_factory=list)
    already_had: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)  # url -> why


async def collect(
    urls: list[str], pages_path: Path, capturer: PageCapturer, *, refresh: bool = False
) -> CollectSummary:
    """Pages that fail to load aren't written, so running collect again retries them."""
    summary = CollectSummary()
    existing = load_pages(pages_path)
    for url in urls:
        if page_id(url) in existing and not refresh:
            summary.already_had.append(url)
            continue
        try:
            result = await capturer.capture(url, "mobile")
        except (CaptureError, SsrfError) as exc:
            summary.failed[url] = str(exc)
            continue
        capture = result.capture
        text = PageText.from_capture(capture)
        if text is None:
            summary.failed[url] = capture.navigation.error or "the page didn't load"
            continue
        append_jsonl(
            pages_path,
            FrozenPage(
                page_id=page_id(url),
                url=url,
                final_url=capture.navigation.final_url,
                http_status=capture.navigation.status,
                collected_at=datetime.now(UTC),
                text=text,
            ),
        )
        summary.collected.append(url)
    return summary
