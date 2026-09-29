"""Re-verify the tracking patterns against live sites:

    python -m tagmonitor.verify_patterns https://site-one.example https://site-two.example

For each URL (real network, no stubs), lists the Meta/Google requests each pattern matched
and, most importantly, requests to tag hosts that matched no pattern: that's how you notice
an endpoint that moved. Traffic we recognize but deliberately don't count (Google Signals,
cookie matching, Floodlight) is only counted, so it doesn't bury the new stuff. Record the
date of a clean run in checks/tracking_patterns.py.
"""

import asyncio
from collections import Counter
from typing import Annotated

import typer
from rich.console import Console

from tagmonitor.browser.capturer import PageCapturer
from tagmonitor.checks.tracking_patterns import classify, is_known_uncounted, is_tag_host
from tagmonitor.config import get_settings
from tagmonitor.page_capture import PageCapture

app = typer.Typer(add_completion=False)
console = Console()


async def capture_all(urls: list[str]) -> list[PageCapture]:
    settings = get_settings()
    captures = []
    async with PageCapturer(policy=settings.ssrf_policy()) as capturer:
        for url in urls:
            captures.append((await capturer.capture(url, "mobile")).capture)
    return captures


@app.command()
def main(urls: Annotated[list[str], typer.Argument(help="Live landing pages to load")]) -> None:
    for capture in asyncio.run(capture_all(urls)):
        console.rule(capture.url)
        if capture.navigation.error_code:
            console.print(f"[red]did not load[/red]: {capture.navigation.error}")
            continue
        tag_requests = [r for r in capture.requests if is_tag_host(r.url)]
        matched = Counter(label for r in tag_requests if (label := classify(r)))
        for label, count in sorted(matched.items()):
            console.print(f"  [green]{label}[/green]: {count}")
        unclassified = [r for r in tag_requests if classify(r) is None]
        if known := sum(is_known_uncounted(r.url) for r in unclassified):
            console.print(f"  [dim]known, deliberately not counted: {known}[/dim]")
        unmatched = [r for r in unclassified if not is_known_uncounted(r.url)]
        if unmatched:
            console.print(f"  [yellow]{len(unmatched)} tag-host requests matched no pattern:[/]")
            for request in unmatched[:25]:
                console.print(f"    {request.method} {request.url[:140]}")


if __name__ == "__main__":
    app()
