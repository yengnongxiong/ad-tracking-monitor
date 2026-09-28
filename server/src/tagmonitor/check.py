"""Check one URL like the monitor would, and print a report:

    python -m tagmonitor.check https://example.com --meta-pixel-id 1234567890

Captures the page on mobile and desktop, runs every check, and shows each check's worst
result with what to do about it. Nothing is stored.
"""

import asyncio
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from tagmonitor.browser.capturer import PageCapturer
from tagmonitor.checks.base import CheckResult, SiteConfig, Status, worst_status
from tagmonitor.checks.explanations import explain
from tagmonitor.checks.registry import ALL_CHECKS, run_checks
from tagmonitor.config import get_settings
from tagmonitor.page_capture import Device

app = typer.Typer(add_completion=False)
console = Console()

STATUS_STYLE: dict[Status, str] = {
    "pass": "[green]pass[/green]",
    "warn": "[yellow]warn[/yellow]",
    "fail": "[red]fail[/red]",
    "info": "[blue]info[/blue]",
    "error": "[dim]n/a[/dim]",
}


async def check_url(site: SiteConfig) -> dict[Device, list[CheckResult]]:
    settings = get_settings()
    results: dict[Device, list[CheckResult]] = {}
    async with PageCapturer(
        policy=settings.ssrf_policy(),
        tracking_stubs=settings.tracking_stubs,
        throttling=settings.capture_throttling,
    ) as capturer:
        for device in ("mobile", "desktop"):
            capture = (await capturer.capture(site.url, device)).capture
            results[device] = run_checks(capture, site)
    return results


@app.command()
def main(
    url: str,
    meta_pixel_id: Annotated[list[str] | None, typer.Option(help="Expected Meta pixel id")] = None,
    ga4_id: Annotated[list[str] | None, typer.Option(help="Expected GA4 id (G-...)")] = None,
    ads_id: Annotated[
        list[str] | None, typer.Option(help="Expected Google Ads id (AW-...)")
    ] = None,
) -> None:
    site = SiteConfig(
        url=url,
        expected_meta_pixel_ids=meta_pixel_id or [],
        expected_ga4_ids=ga4_id or [],
        expected_google_ads_ids=ads_id or [],
    )
    by_device = asyncio.run(check_url(site))

    table = Table(title=f"tag-monitor report for {url}")
    table.add_column("Check")
    table.add_column("Mobile")
    table.add_column("Desktop")
    table.add_column("What we found")
    to_fix: list[CheckResult] = []
    for check in ALL_CHECKS:
        found = {
            device: next((r for r in results if r.check_key == check.check_key), None)
            for device, results in by_device.items()
        }
        ran = [r for r in found.values() if r is not None]
        worst = worst_status(r.status for r in ran)
        headline = next(r for r in ran if r.status == worst)
        cells = [STATUS_STYLE[r.status] if r else "[dim]-[/dim]" for r in found.values()]
        table.add_row(check.title, *cells, headline.summary)
        if worst in ("fail", "warn"):
            to_fix.append(headline)
    console.print(table)

    for result in to_fix:
        explanation = explain(result.check_key, result.code)
        if explanation:
            console.print(f"\n[bold]{result.summary}[/bold]")
            console.print(f"  Why it matters: {explanation.why_it_matters}")
            console.print(f"  How to fix it: {explanation.how_to_fix}")


if __name__ == "__main__":
    app()
