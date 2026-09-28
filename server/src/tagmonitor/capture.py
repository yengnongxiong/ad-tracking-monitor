"""Capture one URL and summarize it: python -m tagmonitor.capture https://example.com

Useful for eyeballing what the browser sees on a real site. Nothing is stored; pass --out to
save the PageCapture JSON (for example as a new fixture in tests/fixtures/captures/).
"""

import asyncio
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from tagmonitor.browser.capturer import CaptureResult, PageCapturer
from tagmonitor.checks.tracking_patterns import is_tracking_hit
from tagmonitor.config import get_settings

app = typer.Typer(add_completion=False)
console = Console()


class DeviceOption(StrEnum):
    mobile = "mobile"
    desktop = "desktop"


async def run_capture(url: str, device: DeviceOption) -> CaptureResult:
    settings = get_settings()
    async with PageCapturer(
        policy=settings.ssrf_policy(),
        tracking_stubs=settings.tracking_stubs,
        throttling=settings.capture_throttling,
    ) as capturer:
        return await capturer.capture(url, device.value)


@app.command()
def main(
    url: str,
    device: Annotated[DeviceOption, typer.Option(help="Device profile")] = DeviceOption.mobile,
    out: Annotated[Path | None, typer.Option(help="Write the PageCapture JSON here")] = None,
    screenshot: Annotated[Path | None, typer.Option(help="Write the JPEG here")] = None,
) -> None:
    result = asyncio.run(run_capture(url, device))
    capture = result.capture
    nav = capture.navigation

    table = Table(show_header=False, box=None)
    table.add_row("URL", capture.url)
    table.add_row("Device", f"{capture.device} ({capture.throttling} throttling)")
    if nav.error_code:
        table.add_row("Navigation", f"[red]{nav.error_code}[/red]: {nav.error}")
    else:
        table.add_row("Status", str(nav.status))
        table.add_row("Final URL", nav.final_url or "")
        table.add_row("Redirects", str(len(nav.redirect_chain)))
    perf = capture.performance
    lcp = f"{perf.lcp_ms / 1000:.2f} s (lab)" if perf.lcp_ms is not None else "n/a"
    table.add_row("LCP", lcp)
    table.add_row("Requests", f"{perf.request_count} ({perf.transfer_bytes / 1024:.0f} KiB)")
    table.add_row("Blocked", ", ".join(f"{b.host} ({b.code})" for b in capture.blocked) or "none")
    table.add_row("JS errors", str(len(capture.page_errors) + len(capture.console_errors)))
    console.print(table)

    hits = [r for r in capture.requests if is_tracking_hit(r.url)]
    if hits:
        console.print(f"\n[bold]{len(hits)} tracking hits[/bold]")
        for request in hits[:20]:
            console.print(f"  {request.method} {request.url[:120]}")

    if out:
        out.write_text(capture.model_dump_json(indent=2))
        console.print(f"\nwrote {out}")
    if screenshot and result.screenshot_jpeg:
        screenshot.write_bytes(result.screenshot_jpeg)
        console.print(f"wrote {screenshot}")


if __name__ == "__main__":
    app()
