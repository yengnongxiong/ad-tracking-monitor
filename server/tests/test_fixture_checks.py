"""M3 acceptance: every fixture site, captured in a real browser, yields the expected results."""

import pytest

from tagmonitor.browser.capturer import PageCapturer
from tagmonitor.checks.registry import run_checks
from tests.fixture_expectations import EXPECTED, EXTRA_HEALTH_FINDINGS, expected_for, site_config
from tests.fixture_server import FixtureServer


@pytest.mark.parametrize("site", sorted(EXPECTED))
async def test_fixture_site_statuses(
    site: str, capturer: PageCapturer, fixture_server: FixtureServer
) -> None:
    url = fixture_server.url(site)
    capture = (await capturer.capture(url, "mobile")).capture
    results = {r.check_key: r for r in run_checks(capture, site_config(site, url))}

    assert {key: (r.status, r.code) for key, r in results.items()} == expected_for(site)
    if site in EXTRA_HEALTH_FINDINGS:
        findings = [f["code"] for f in results["page_health"].details["findings"]]
        assert EXTRA_HEALTH_FINDINGS[site] in findings


async def test_desktop_runs_tag_and_health_checks_only(
    capturer: PageCapturer, fixture_server: FixtureServer
) -> None:
    url = fixture_server.url("meta_ok")
    capture = (await capturer.capture(url, "desktop")).capture
    results = {r.check_key: (r.status, r.code) for r in run_checks(capture, site_config("", url))}
    assert results["meta_pixel"] == ("pass", "firing")
    assert "page_speed" not in results and "mobile_render" not in results
