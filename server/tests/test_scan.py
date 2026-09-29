"""The research scan (PRD §16): targets, robots.txt, the scan job, and aggregate findings."""

from pathlib import Path

import pandas as pd
import pytest

from tagmonitor.browser.capturer import PageCapturer
from tagmonitor.browser.ssrf import SsrfPolicy
from tagmonitor.db.pool import Pool
from tagmonitor.scan import runner
from tagmonitor.scan.analysis import (
    MIN_CELL,
    compute_findings,
    format_seconds,
    render_markdown,
    write_findings,
)
from tagmonitor.scan.robots import check_robots
from tagmonitor.scan.runner import start_scan
from tagmonitor.scan.targets import is_domain_name, load_targets
from tagmonitor.stats import wilson_interval
from tagmonitor.storage import ObjectStorage
from tests.fixture_server import FixtureServer, StaticResolver
from tests.test_worker import make_worker, run_until

# Several "businesses", all served by the local fixture server under their own hostnames.
HOSTS = ["shop-a.test", "shop-b.test", "shop-c.test", "no-robots.test", "robots-500.test"]
POLICY = SsrfPolicy(allow_hosts=frozenset(HOSTS))
RESOLVER = StaticResolver({host: ["127.0.0.1"] for host in HOSTS})


def write_csv(tmp_path: Path, rows: list[str]) -> Path:
    path = tmp_path / "targets.csv"
    path.write_text("﻿url,category,source\n" + "\n".join(rows) + "\n")  # with a BOM
    return path


# -- targets --------------------------------------------------------------------------------------


def test_targets_are_vetted_and_deduplicated_by_domain(tmp_path: Path) -> None:
    csv = write_csv(
        tmp_path,
        [
            "https://www.bakery.example.com/,bakery,chamber",
            "bakery.example.com/order,bakery,chamber",  # same registrable domain
            "florist.example.org,,chamber",  # bare domain, no category
            "ftp://files.example.net/,other,chamber",  # not http(s)
            "http://10.0.0.1/,other,chamber",  # directories list domains, not IP addresses
            "https://not a domain/,other,chamber",  # a typo, not an unreachable business
            "café.example.fr,cafe,chamber",  # IDNs are fine
            ",bakery,chamber",  # empty row
        ],
    )
    targets = load_targets(csv, SsrfPolicy())
    outcome = [(t.url, t.category, t.skip_reason) for t in targets]
    assert outcome == [
        ("https://www.bakery.example.com/", "bakery", None),
        ("https://bakery.example.com/order", "bakery", "duplicate_domain"),
        ("https://florist.example.org", "uncategorized", None),
        ("ftp://files.example.net/", "other", "invalid_url"),
        ("http://10.0.0.1/", "other", "invalid_url"),
        ("https://not a domain/", "other", "invalid_url"),
        ("https://café.example.fr", "cafe", None),
    ]


@pytest.mark.parametrize(
    ("host", "ok"),
    [
        ("example.com", True),
        ("www.my-bakery.co.uk", True),
        ("beanthere.demo", True),
        ("xn--caf-dma.fr", True),
        ("café.fr", True),
        ("example.com.", True),
        ("localhost", False),
        ("192.168.1.1", False),
        ("[::1]", False),
        ("-bad.example.com", False),
        ("bad_.example.com", False),
        ("two words.com", False),
        ("a" * 64 + ".com", False),
    ],
)
def test_is_domain_name(host: str, ok: bool) -> None:
    assert is_domain_name(host) is ok


def test_targets_file_needs_the_three_columns(tmp_path: Path) -> None:
    path = tmp_path / "bad.csv"
    path.write_text("url,name\nhttps://a.example/,A\n")
    with pytest.raises(ValueError, match="category, source"):
        load_targets(path, SsrfPolicy())


# -- robots.txt -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("host", "path", "outcome"),
    [
        ("shop-a.test", "/meta_ok/", "allowed"),
        ("shop-a.test", "/private-area/page", "disallowed"),  # disallowed for everyone
        ("shop-a.test", "/no-tagmonitor/", "disallowed"),  # disallowed for our token only
        ("no-robots.test", "/private-area/", "allowed"),  # 404 robots.txt = no rules
        ("robots-500.test", "/meta_ok/", "robots_error"),  # 5xx = assume disallowed
        ("unknown.test", "/", "unreachable"),  # DNS fails inside our egress proxy
    ],
)
async def test_robots_verdicts(
    fixture_server: FixtureServer, host: str, path: str, outcome: str
) -> None:
    url = f"http://{host}:{fixture_server.port}{path}"
    verdict = await check_robots(url, POLICY, RESOLVER)
    assert verdict.outcome == outcome, verdict.detail


async def test_robots_fetch_is_ssrf_guarded(fixture_server: FixtureServer) -> None:
    fixture_server.hits.clear()
    verdict = await check_robots(f"http://127.0.0.1:{fixture_server.port}/", POLICY, RESOLVER)
    assert verdict.outcome == "unreachable"
    assert "ssrf_blocked" in verdict.detail
    assert fixture_server.hits == []


# -- analysis -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("successes", "n", "low", "high"),
    [
        (8, 10, 0.4902, 0.9433),  # textbook example
        (0, 10, 0.0, 0.2775),  # doesn't collapse to [0, 0]
        (10, 10, 0.7225, 1.0),
        (50, 100, 0.4038, 0.5962),
    ],
)
def test_wilson_interval(successes: int, n: int, low: float, high: float) -> None:
    got_low, got_high = wilson_interval(successes, n)
    assert got_low == pytest.approx(low, abs=1e-4)
    assert got_high == pytest.approx(high, abs=1e-4)


@pytest.mark.parametrize("n", range(1, 60))
def test_wilson_interval_always_contains_the_estimate(n: int) -> None:
    """Regression: at 0/n floating point put the lower bound above 0, which crashed the
    error-bar chart with a negative error."""
    for successes in (0, n // 3, n):
        low, high = wilson_interval(successes, n)
        assert 0.0 <= low <= successes / n <= high <= 1.0


def synthetic_scan(n: int, category: str = "bakery") -> pd.DataFrame:
    rows = []
    for i in range(n):
        rows.append(
            {
                "id": i,
                "category": category,
                "source": "chamber",
                "status": "done",
                "skip_reason": None,
                "http_status": 200,
                "navigation_error": None,
                "meta_pixel": "firing" if i % 2 == 0 else "not_installed",
                "google_ga4": "firing",
                "google_ads": "not_installed",
                "google_gtm": "loaded" if i < 2 else "not_installed",
                "mobile_render": "ok",
                "lcp_ms": 1500.0 + i * 1000,
            }
        )
    return pd.DataFrame(rows).set_index("id")


def test_small_categories_are_not_published() -> None:
    frame = pd.concat([synthetic_scan(MIN_CELL, "bakery"), synthetic_scan(2, "florist")])
    frame.index = range(len(frame))
    findings = compute_findings(frame)
    assert findings.by_category["category"].tolist() == ["bakery"]


# -- end to end ------------------------------------------------------------------------------


async def test_scan_end_to_end(
    db: Pool,
    storage: ObjectStorage,
    fixture_server: FixtureServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner, "SAME_DOMAIN_GAP_S", 0)  # no 10 s wait in tests
    port = fixture_server.port
    csv = write_csv(
        tmp_path,
        [
            f"http://shop-a.test:{port}/meta_ok/,bakery,chamber",
            f"http://shop-b.test:{port}/meta_not_firing/,bakery,chamber",
            f"http://shop-c.test:{port}/no_tags/,florist,chamber",
            f"http://shop-a.test:{port}/ga4_ok/,bakery,chamber",  # second page, same domain
            f"http://no-robots.test:{port}/private-area/,florist,bni",  # 404 page, allowed
            f"http://robots-500.test:{port}/meta_ok/,florist,bni",
            "http://unknown.test/,florist,bni",  # port 80 so it passes vetting; DNS then fails
        ],
    )
    async with db.connection() as conn:
        targets = load_targets(csv, POLICY)
        plan = await start_scan(
            conn, "test-scan", targets, POLICY, RESOLVER, {"throttling": "none"}
        )
    assert plan.counts == {
        "enqueued": 4,
        "skipped:duplicate_domain": 1,
        "skipped:robots_error": 1,
        "failed:unreachable": 1,
    }

    async with PageCapturer(policy=POLICY, resolver=RESOLVER, tracking_stubs=True) as capturer:
        worker = make_worker(db, capturer, storage)

        async def scan_done() -> bool:
            async with db.connection() as conn:
                cursor = await conn.execute(
                    "SELECT count(*) AS n FROM scan_targets WHERE status = 'pending'"
                )
                row = await cursor.fetchone()
            return row is not None and row["n"] == 0

        await run_until([worker], scan_done)

    async with db.connection() as conn:
        findings = await write_findings(conn, "test-scan", tmp_path)

    assert (findings.listed, findings.attempted, findings.reachable, findings.loaded) == (
        7,
        5,
        4,
        3,
    )
    shares = {p.label: (p.successes, p.n) for p in findings.proportions}
    assert shares["Sites reachable (of attempted)"] == (4, 5)
    assert shares["HTTP error page (of reachable)"] == (1, 4)  # the 404 page
    assert shares["Meta Pixel script present"] == (2, 3)
    assert shares["…of those, Meta firing PageView"] == (1, 2)
    assert len(findings.lcp_seconds) == 3

    report = (tmp_path / "findings.md").read_text()
    assert "Sites reachable (of attempted) | 80% (4/5)" in report
    assert "## Methodology" in report and "## Limitations" in report
    assert "shop-" not in report and "://" not in report  # aggregates only: no URLs, no hosts
    assert (tmp_path / "findings" / "rates.png").stat().st_size > 1000
    assert (tmp_path / "findings" / "lcp_histogram.png").exists()


def test_report_rows_show_share_interval_and_n() -> None:
    frame = synthetic_scan(6)
    findings = compute_findings(frame)
    scan = {"name": "x", "created_at": pd.Timestamp("2026-10-01", tz="UTC"), "config": {}}
    report = render_markdown(scan, findings, "findings")
    assert "| Meta Pixel script present | 50% (3/6) | 19% to 81% | 6 |" in report
    assert "://" not in report


@pytest.mark.parametrize(
    ("value", "text"), [(0.04, "under 0.1 s"), (0.1, "0.1 s"), (2.46, "2.5 s")]
)
def test_median_lcp_wording(value: float, text: str) -> None:
    assert format_seconds(value) == text
