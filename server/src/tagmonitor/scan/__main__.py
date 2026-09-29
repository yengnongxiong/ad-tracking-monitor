"""Research scan CLI (PRD §16).

python -m tagmonitor.scan run --targets data/scan/targets.csv --name fall-2026 --wait
python -m tagmonitor.scan analyze --name fall-2026
"""

import asyncio
from pathlib import Path
from typing import Annotated

import typer
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from rich.console import Console
from rich.table import Table

from tagmonitor.browser.ssrf import SystemResolver
from tagmonitor.config import get_settings
from tagmonitor.scan.analysis import write_findings
from tagmonitor.scan.runner import load_scan_config, scan_progress, start_scan
from tagmonitor.scan.targets import load_targets

app = typer.Typer(add_completion=False)
console = Console()


@app.command()
def run(
    targets: Annotated[Path, typer.Option(help="CSV with url, category, source columns")],
    name: Annotated[str, typer.Option(help="A unique name for this scan, e.g. fall-2026")],
    wait: Annotated[bool, typer.Option(help="Wait for the workers to finish")] = False,
) -> None:
    """Vet the targets, check robots.txt, and queue one mobile page load per domain."""
    settings = get_settings()

    async def go() -> None:
        planned = load_targets(targets, settings.ssrf_policy())
        async with await AsyncConnection.connect(
            settings.database_url, autocommit=True, row_factory=dict_row
        ) as conn:
            plan = await start_scan(
                conn,
                name,
                planned,
                settings.ssrf_policy(),
                SystemResolver(),
                load_scan_config(targets, settings.capture_throttling),
            )
            table = Table("Outcome", "Targets", title=f"Scan {name}")
            for outcome, n in sorted(plan.counts.items()):
                table.add_row(outcome, str(n))
            console.print(table)
            while wait:
                progress = await scan_progress(conn, plan.scan_id)
                console.print(
                    f"pending {progress['pending']}, done {progress['done']}, "
                    f"failed {progress['failed']}"
                )
                if progress["pending"] == 0:
                    break
                await asyncio.sleep(15)

    asyncio.run(go())


@app.command()
def analyze(
    name: Annotated[str, typer.Option(help="The scan to analyze")],
    docs_dir: Annotated[Path, typer.Option(help="Where findings.md goes")] = Path("../docs"),
) -> None:
    """Write docs/findings.md and docs/findings/*.png from a finished scan."""
    settings = get_settings()

    async def go() -> None:
        async with await AsyncConnection.connect(
            settings.database_url, autocommit=True, row_factory=dict_row
        ) as conn:
            findings = await write_findings(conn, name, docs_dir)
        console.print(f"wrote {docs_dir / 'findings.md'} ({findings.loaded} sites loaded)")

    asyncio.run(go())


if __name__ == "__main__":
    app()
