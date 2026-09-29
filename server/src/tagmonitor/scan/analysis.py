"""Turn a finished scan into aggregate findings: docs/findings.md + charts (PRD §16).

Only aggregates are ever written: counts, shares and 95% Wilson intervals. No URLs, no
business names, and categories with fewer than MIN_CELL loaded sites are left out so a
small category can't point at a particular business.
"""

import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # render to files, no display needed

import matplotlib.pyplot as plt  # after matplotlib.use()
import pandas as pd

from tagmonitor.checks.page_speed import GOOD_LCP_MS, POOR_LCP_MS
from tagmonitor.queue.jobs import Conn
from tagmonitor.stats import wilson_interval

MIN_CELL = 5
FIRING = {"firing", "duplicate_pageview"}


@dataclass(frozen=True)
class Proportion:
    label: str
    successes: int
    n: int

    @property
    def share(self) -> float:
        return self.successes / self.n if self.n else math.nan

    @property
    def interval(self) -> tuple[float, float]:
        return wilson_interval(self.successes, self.n)

    def row(self) -> str:
        if self.n == 0:
            return f"| {self.label} | n/a | n/a | 0 |"
        low, high = self.interval
        return (
            f"| {self.label} | {self.share:.0%} ({self.successes}/{self.n}) | "
            f"{low:.0%} to {high:.0%} | {self.n} |"
        )


async def load_scan(conn: Conn, name: str) -> tuple[dict[str, Any], pd.DataFrame]:
    """One row per target: its status, the page load's outcome, and each check's code."""
    cursor = await conn.execute(
        "SELECT id, name, created_at, config FROM scans WHERE name = %s", (name,)
    )
    scan = await cursor.fetchone()
    if scan is None:
        raise ValueError(f"no scan named {name!r}")
    cursor = await conn.execute(
        """
        SELECT t.id, t.category, t.source, t.status, t.skip_reason,
               r.http_status, r.error_code AS navigation_error, r.started_at,
               cr.check_key, cr.code, cr.details->>'lcp_ms' AS lcp_ms
        FROM scan_targets t
        LEFT JOIN check_runs r ON r.id = t.run_id
        LEFT JOIN check_results cr ON cr.run_id = r.id
        WHERE t.scan_id = %s
        """,
        (scan["id"],),
    )
    rows = await cursor.fetchall()
    long = pd.DataFrame(rows)
    if long.empty:
        return dict(scan), pd.DataFrame()
    base = long.drop(columns=["check_key", "code", "lcp_ms"]).drop_duplicates("id")
    codes = long.dropna(subset=["check_key"]).pivot(index="id", columns="check_key", values="code")
    lcp = long[long["check_key"] == "page_speed"].set_index("id")["lcp_ms"].astype(float)
    frame = base.set_index("id").join(codes).join(lcp.rename("lcp_ms"))
    return dict(scan), frame


@dataclass
class Findings:
    listed: int
    attempted: int
    reachable: int
    loaded: int  # reachable and HTTP status below 400: the population for tag/speed metrics
    skipped: dict[str, int]
    proportions: list[Proportion]
    lcp_seconds: list[float]
    by_category: pd.DataFrame
    sources: dict[str, int]


def _count(mask: pd.Series) -> int:
    return int(mask.sum())


def compute_findings(frame: pd.DataFrame) -> Findings:
    def code(column: str) -> pd.Series:
        return frame[column] if column in frame else pd.Series(None, index=frame.index)

    attempted = frame["status"].isin(["done", "failed"])
    reachable = (frame["status"] == "done") & frame["navigation_error"].isna()
    loaded = reachable & (frame["http_status"].fillna(0) < 400)
    meta = code("meta_pixel")
    has_meta = loaded & meta.notna() & ~meta.isin(["not_installed", "not_evaluated"])
    ga4, ads, gtm = code("google_ga4"), code("google_ads"), code("google_gtm")
    layout = code("mobile_render")

    proportions = [
        Proportion("Sites reachable (of attempted)", _count(reachable), _count(attempted)),
        Proportion(
            "HTTP error page (of reachable)", _count(reachable & ~loaded), _count(reachable)
        ),
        Proportion("Meta Pixel script present", _count(has_meta), _count(loaded)),
        Proportion(
            "…of those, Meta firing PageView",
            _count(has_meta & meta.isin(FIRING)),
            _count(has_meta),
        ),
        Proportion(
            "…of those, PageView sent twice or more",
            _count(has_meta & (meta == "duplicate_pageview")),
            _count(has_meta),
        ),
        Proportion("GA4 firing", _count(loaded & ga4.isin(FIRING)), _count(loaded)),
        Proportion(
            "Google Ads tag present",
            _count(loaded & ads.notna() & (ads != "not_installed")),
            _count(loaded),
        ),
        Proportion(
            "Google Tag Manager present",
            _count(loaded & gtm.notna() & (gtm != "not_installed")),
            _count(loaded),
        ),
        Proportion(
            "Horizontal overflow on mobile",
            _count(loaded & (layout == "horizontal_overflow")),
            _count(loaded),
        ),
        Proportion(
            "Missing viewport meta tag",
            _count(loaded & (layout == "missing_viewport")),
            _count(loaded),
        ),
    ]
    lcp = frame.loc[loaded, "lcp_ms"].dropna() / 1000 if "lcp_ms" in frame else pd.Series()

    categories = []
    for category, group in frame[loaded].groupby("category"):
        if len(group) < MIN_CELL:
            continue  # too small to publish without pointing at individual businesses
        group_meta = group["meta_pixel"] if "meta_pixel" in group else pd.Series(dtype=object)
        group_ga4 = group["google_ga4"] if "google_ga4" in group else pd.Series(dtype=object)
        group_lcp = group["lcp_ms"].dropna() if "lcp_ms" in group else pd.Series(dtype=float)
        categories.append(
            {
                "category": category,
                "sites": len(group),
                "meta_firing": group_meta.isin(FIRING).mean(),
                "ga4_firing": group_ga4.isin(FIRING).mean(),
                "lcp_good": (group_lcp <= GOOD_LCP_MS).mean() if len(group_lcp) else math.nan,
            }
        )

    skipped = frame.loc[frame["status"] == "skipped", "skip_reason"].value_counts().to_dict()
    return Findings(
        listed=len(frame),
        attempted=_count(attempted),
        reachable=_count(reachable),
        loaded=_count(loaded),
        skipped={str(k): int(v) for k, v in skipped.items()},
        proportions=proportions,
        lcp_seconds=[float(x) for x in lcp],
        by_category=pd.DataFrame(categories),
        sources={str(k): int(v) for k, v in frame["source"].value_counts().items()},
    )


def plot_lcp_histogram(seconds: list[float], path: Path) -> None:
    figure, axes = plt.subplots(figsize=(7, 3.5), dpi=150)
    capped = [min(value, 15.0) for value in seconds]  # a few very slow pages shouldn't squash it
    axes.hist(capped, bins=30, color="#3f3f46")
    for threshold, label, color in (
        (GOOD_LCP_MS / 1000, "good ≤ 2.5 s", "#10b981"),
        (POOR_LCP_MS / 1000, "poor > 4 s", "#dc2626"),
    ):
        axes.axvline(threshold, color=color, linestyle="--", linewidth=1.5, label=label)
    axes.set_xlabel("Largest Contentful Paint, mobile, lab (seconds; capped at 15)")
    axes.set_ylabel("Sites")
    axes.legend(frameon=False)
    axes.spines[["top", "right"]].set_visible(False)
    figure.tight_layout()
    figure.savefig(path)
    plt.close(figure)


def plot_rates(proportions: list[Proportion], path: Path) -> None:
    shown = [p for p in proportions if p.n > 0]
    figure, axes = plt.subplots(figsize=(7, 0.45 * len(shown) + 1), dpi=150)
    labels = [p.label for p in shown]
    shares = [p.share * 100 for p in shown]
    low = [(p.share - p.interval[0]) * 100 for p in shown]
    high = [(p.interval[1] - p.share) * 100 for p in shown]
    axes.barh(labels, shares, xerr=[low, high], color="#3f3f46", ecolor="#a1a1aa", capsize=3)
    axes.invert_yaxis()
    axes.set_xlim(0, 100)
    axes.set_xlabel("% of sites (bars: 95% Wilson interval)")
    axes.spines[["top", "right"]].set_visible(False)
    figure.tight_layout()
    figure.savefig(path)
    plt.close(figure)


def format_seconds(value: float) -> str:
    return "under 0.1 s" if value < 0.1 else f"{value:.1f} s"


def render_markdown(scan: dict[str, Any], findings: Findings, charts_dir: str) -> str:
    config = scan["config"] or {}
    created: datetime = scan["created_at"]
    lcp = pd.Series(findings.lcp_seconds, dtype=float)
    speed = [
        Proportion("LCP good (≤ 2.5 s)", int((lcp <= GOOD_LCP_MS / 1000).sum()), len(lcp)),
        Proportion(
            "LCP needs improvement (2.5 to 4 s)",
            int(((lcp > GOOD_LCP_MS / 1000) & (lcp <= POOR_LCP_MS / 1000)).sum()),
            len(lcp),
        ),
        Proportion("LCP poor (> 4 s)", int((lcp > POOR_LCP_MS / 1000).sum()), len(lcp)),
    ]
    lines = [
        f"# Findings: {scan['name']}",
        "",
        "> Generated by `python -m tagmonitor.scan analyze` from a real scan. Aggregates only:",
        "> no URLs or business names are published.",
        "",
        f"- Scan started: {created:%Y-%m-%d} (UTC)",
        f"- Sites in the target list: {findings.listed}",
        f"- Attempted: {findings.attempted}; reachable: {findings.reachable}; "
        f"loaded without an HTTP error: {findings.loaded}",
    ]
    if findings.skipped:
        skipped = ", ".join(f"{reason}: {n}" for reason, n in sorted(findings.skipped.items()))
        lines.append(f"- Not loaded (skipped): {skipped}")
    lines += [
        "",
        "## Headline numbers",
        "",
        "Tag and layout shares use the sites that loaded without an HTTP error as the "
        "denominator unless the row says otherwise.",
        "",
        "| Measure | Share | 95% CI | n |",
        "|---|---|---|---|",
        *(p.row() for p in findings.proportions),
        "",
        f"![Shares with 95% intervals]({charts_dir}/rates.png)",
        "",
        "## Mobile speed",
        "",
    ]
    if len(lcp):
        lines += [
            f"Median lab LCP: {format_seconds(lcp.median())} across {len(lcp)} "
            f"{'site' if len(lcp) == 1 else 'sites'}.",
            "",
            "| Measure | Share | 95% CI | n |",
            "|---|---|---|---|",
            *(p.row() for p in speed),
            "",
            f"![LCP histogram]({charts_dir}/lcp_histogram.png)",
            "",
        ]
    else:
        lines += ["No speed measurements.", ""]
    lines += ["## By category", ""]
    if findings.by_category.empty:
        lines += [f"No category had at least {MIN_CELL} loaded sites.", ""]
    else:
        lines += [
            f"Only categories with at least {MIN_CELL} loaded sites are shown.",
            "",
            "| Category | Sites | Meta firing | GA4 firing | LCP good |",
            "|---|---|---|---|---|",
        ]
        for row in findings.by_category.sort_values("sites", ascending=False).itertuples():
            lines.append(
                f"| {row.category} | {row.sites} | {row.meta_firing:.0%} | "
                f"{row.ga4_firing:.0%} | {row.lcp_good:.0%} |"
            )
        lines.append("")
    source_list = ", ".join(f"{name} ({n})" for name, n in findings.sources.items())
    throttling = config.get("throttling", "none")
    lines += [
        "## Methodology",
        "",
        f"- **Sample.** {findings.listed} small-business websites compiled by hand from "
        f"public directories (sources: {source_list or 'n/a'}). One page per registrable "
        "domain.",
        "- **Measurement.** One page load per site in headless Chromium using Playwright's "
        f"Pixel 7 profile (Chrome's mobile user agent), throttling: {throttling}. Tags are "
        "detected from the network requests the page makes (the same checks as monitoring).",
        f"- **Politeness.** robots.txt honored for the `{config.get('robots_user_agent', '')}` "
        "token; at most one page load per domain at a time, and at least 10 seconds after "
        "the robots.txt request.",
        "- **Statistics.** Shares with 95% Wilson score intervals.",
        "",
        "## Limitations",
        "",
        "- **Sample source bias.** Directory members aren't a random sample of small "
        "businesses (they skew toward established, local, directory-joining firms), so "
        "shares don't generalize to all small businesses.",
        "- **Single page loads.** Each site was loaded once, on one day. A slow CDN moment or "
        "a flaky third-party script shows up as-is.",
        "- **Lab, not field.** LCP comes from one lab page load, not real visitors' phones "
        "(Chrome UX Report field data would be the complement).",
        "- **Consent banners.** Banners weren't clicked. Tags that wait for consent look "
        "'not firing', which inflates 'installed but not firing' for sites whose banner "
        "targets our location.",
        f"- **Date.** Tags and sites change constantly; these numbers describe {created:%B %Y}.",
        "- **User agent.** A standard mobile Chrome user agent, so sites behave as for "
        "visitors; some bot defenses may still have served a different page.",
        "- **Not visible from a browser.** Server-side tracking (Meta Conversions API, "
        "server-side Tag Manager on a first-party domain) isn't counted.",
        "",
    ]
    return "\n".join(lines)


def summary(scan: dict[str, Any], findings: Findings) -> dict[str, Any]:
    """The headline numbers as data, for the landing page: the same aggregates as findings.md,
    nothing per site."""
    lcp = findings.lcp_seconds
    return {
        "scan": scan["name"],
        "date": f"{scan['created_at']:%Y-%m-%d}",
        "attempted": findings.attempted,
        "loaded": findings.loaded,
        "median_lcp_s": float(pd.Series(lcp).median()) if lcp else None,
        "proportions": [
            {
                "label": p.label,
                "successes": p.successes,
                "n": p.n,
                "share": p.share if p.n else None,
                "ci": list(p.interval) if p.n else None,
            }
            for p in findings.proportions
        ],
    }


async def write_findings(
    conn: Conn, name: str, docs_dir: Path, web_dir: Path | None = None
) -> Findings:
    """docs/findings.md, its charts and summary.json; with web_dir, also the landing page's
    copy of the summary (web/public/findings.json)."""
    scan, frame = await load_scan(conn, name)
    if frame.empty:
        raise ValueError(f"scan {name!r} has no targets")
    findings = compute_findings(frame)
    write_outputs(scan, findings, docs_dir, web_dir)
    return findings


def write_outputs(
    scan: dict[str, Any], findings: Findings, docs_dir: Path, web_dir: Path | None
) -> None:
    charts = docs_dir / "findings"
    charts.mkdir(parents=True, exist_ok=True)
    plot_rates(findings.proportions, charts / "rates.png")
    if findings.lcp_seconds:
        plot_lcp_histogram(findings.lcp_seconds, charts / "lcp_histogram.png")
    (docs_dir / "findings.md").write_text(render_markdown(scan, findings, "findings"))
    data = json.dumps(summary(scan, findings), indent=2) + "\n"
    (charts / "summary.json").write_text(data)
    if web_dir is not None:
        web_dir.mkdir(parents=True, exist_ok=True)
        (web_dir / "findings.json").write_text(data)
