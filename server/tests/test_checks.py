"""Analyzer unit tests on hand-built captures: every code, boundary and edge case, no browser."""

import pytest

from tagmonitor.checks.base import Check, CheckResult, SiteConfig, Status, worst_status
from tagmonitor.checks.explanations import EXPLANATIONS, explain, render_markdown
from tagmonitor.checks.google_tags import (
    GoogleAdsCheck,
    GoogleAnalyticsCheck,
    GoogleTagManagerCheck,
)
from tagmonitor.checks.meta_pixel import MetaPixelCheck
from tagmonitor.checks.mobile_render import MobileRenderCheck
from tagmonitor.checks.page_health import PageHealthCheck
from tagmonitor.checks.page_speed import PageSpeedCheck
from tagmonitor.checks.registry import ALL_CHECKS, CAPTURE_CHECKS, run_checks
from tagmonitor.page_capture import PageCapture
from tests.capture_builders import (
    FBEVENTS,
    SITE_URL,
    ads_hit,
    capture,
    dom,
    ga4_hit,
    gtag,
    gtm,
    meta_hit,
    req,
)

SITE = SiteConfig(url=SITE_URL)
SHARED_CODES = {"not_evaluated", "check_crashed"}


def run(check: Check, page: PageCapture, site: SiteConfig = SITE) -> CheckResult:
    """Analyze, and insist the code is one the check declares (so it has an explanation)."""
    result = check.analyze(page, site)
    assert result.check_key == check.check_key
    assert result.code in check.codes | SHARED_CODES, result.code
    return result


def outcome(result: CheckResult) -> tuple[Status, str]:
    return result.status, result.code


# -- Meta Pixel ------------------------------------------------------------------------------

META = MetaPixelCheck()


def test_meta_firing() -> None:
    result = run(META, capture(req(FBEVENTS), meta_hit("111")))
    assert outcome(result) == ("pass", "firing")
    assert result.details["ids"] == ["111"]
    assert result.details["events"] == [{"id": "111", "event": "PageView", "count": 1}]


def test_meta_not_installed_is_info_unless_expected() -> None:
    assert outcome(run(META, capture())) == ("info", "not_installed")
    expected = SiteConfig(url=SITE_URL, expected_meta_pixel_ids=["111"])
    assert outcome(run(META, capture(), expected)) == ("fail", "not_installed")


def test_meta_installed_not_firing() -> None:
    assert outcome(run(META, capture(req(FBEVENTS)))) == ("fail", "installed_not_firing")


def test_meta_other_events_without_pageview_is_not_firing() -> None:
    page = capture(req(FBEVENTS), meta_hit("111", "ViewContent"))
    assert outcome(run(META, page)) == ("fail", "installed_not_firing")


def test_meta_script_blocked() -> None:
    page = capture(req(FBEVENTS, failure="net::ERR_BLOCKED_BY_CLIENT"))
    result = run(META, page)
    assert outcome(result) == ("fail", "script_blocked")
    assert "ERR_BLOCKED_BY_CLIENT" in result.summary


def test_meta_script_http_error_is_blocked() -> None:
    assert outcome(run(META, capture(req(FBEVENTS, status=403)))) == ("fail", "script_blocked")


def test_meta_duplicate_pageview() -> None:
    page = capture(req(FBEVENTS), meta_hit("111"), meta_hit("111"))
    result = run(META, page)
    assert outcome(result) == ("warn", "duplicate_pageview")
    assert "2 times" in result.summary


def test_two_pixels_one_pageview_each_is_not_a_duplicate() -> None:
    page = capture(req(FBEVENTS), meta_hit("111"), meta_hit("222"))
    assert outcome(run(META, page)) == ("pass", "firing")


def test_meta_wrong_pixel() -> None:
    site = SiteConfig(url=SITE_URL, expected_meta_pixel_ids=["111"])
    result = run(META, capture(req(FBEVENTS), meta_hit("999")), site)
    assert outcome(result) == ("fail", "wrong_pixel")
    assert result.details["missing_ids"] == ["111"]


def test_meta_one_of_two_expected_pixels_missing_is_wrong_pixel() -> None:
    site = SiteConfig(url=SITE_URL, expected_meta_pixel_ids=["111", "222"])
    assert outcome(run(META, capture(req(FBEVENTS), meta_hit("111")), site))[1] == "wrong_pixel"


def test_meta_expected_pixel_seen_only_in_config_request_is_not_firing() -> None:
    config = req("https://connect.facebook.net/signals/config/111?v=2.9")
    site = SiteConfig(url=SITE_URL, expected_meta_pixel_ids=["111"])
    page = capture(req(FBEVENTS), config)
    assert outcome(run(META, page, site)) == ("fail", "installed_not_firing")


def test_meta_post_hits_count() -> None:
    hit = req("https://www.facebook.com/tr/", method="POST", post_data="id=111&ev=PageView")
    assert outcome(run(META, capture(req(FBEVENTS), hit))) == ("pass", "firing")


def test_meta_hits_that_never_left_the_browser_do_not_count() -> None:
    blocked_hit = req(
        "https://www.facebook.com/tr/?id=111&ev=PageView", failure="net::ERR_BLOCKED_BY_CLIENT"
    )
    page = capture(req(FBEVENTS), blocked_hit)
    assert outcome(run(META, page)) == ("fail", "installed_not_firing")


def test_meta_not_evaluated_when_page_did_not_load() -> None:
    result = run(META, capture(status=None, error_code="timeout"))
    assert outcome(result) == ("error", "not_evaluated")
    assert result.details["navigation_error"] == "timeout"


# -- GA4 --------------------------------------------------------------------------------------

GA4 = GoogleAnalyticsCheck()


def test_ga4_firing_via_gtag() -> None:
    assert outcome(run(GA4, capture(gtag("G-ABC"), ga4_hit("G-ABC")))) == ("pass", "firing")


def test_ga4_firing_via_gtm_without_a_gtag_script_request() -> None:
    assert outcome(run(GA4, capture(gtm("GTM-1"), ga4_hit("G-ABC")))) == ("pass", "firing")


def test_ga4_installed_not_firing() -> None:
    assert outcome(run(GA4, capture(gtag("G-ABC")))) == ("fail", "installed_not_firing")


def test_ga4_duplicate_pageview() -> None:
    page = capture(gtag("G-ABC"), ga4_hit("G-ABC"), ga4_hit("G-ABC"))
    assert outcome(run(GA4, page)) == ("warn", "duplicate_pageview")


def test_ga4_wrong_id_and_case_insensitive_expected_ids() -> None:
    page = capture(gtag("G-ABC"), ga4_hit("G-ABC"))
    assert outcome(run(GA4, page, SiteConfig(url=SITE_URL, expected_ga4_ids=["g-abc"]))) == (
        "pass",
        "firing",
    )
    wrong = run(GA4, page, SiteConfig(url=SITE_URL, expected_ga4_ids=["G-OTHER"]))
    assert outcome(wrong) == ("fail", "wrong_id")


def test_ga4_batched_post_counts() -> None:
    batched = req(
        "https://region1.google-analytics.com/g/collect?v=2&tid=G-ABC",
        method="POST",
        post_data="en=page_view\r\nen=scroll",
    )
    assert outcome(run(GA4, capture(gtag("G-ABC"), batched))) == ("pass", "firing")


def test_ga4_loaded_as_a_destination_but_silent_is_installed_not_firing() -> None:
    """Regression: GTM-managed GA4 often loads only via gtag/destination (seen live). Without
    recognizing it, a silent GA4 tag looked "not installed" instead of broken."""
    page = capture(gtm("GTM-1"), req("https://www.googletagmanager.com/gtag/destination?id=G-ABC"))
    assert outcome(run(GA4, page)) == ("fail", "installed_not_firing")


def test_google_tag_gt_id_alone_is_not_ga4() -> None:
    assert outcome(run(GA4, capture(gtag("GT-XYZ")))) == ("info", "not_installed")


def test_ga4_script_blocked() -> None:
    page = capture(req("https://www.googletagmanager.com/gtag/js?id=G-ABC", status=404))
    assert outcome(run(GA4, page)) == ("fail", "script_blocked")


# -- Google Ads ---------------------------------------------------------------------------------

ADS = GoogleAdsCheck()


def test_ads_firing_and_id_normalization() -> None:
    page = capture(gtag("AW-123"), ads_hit("123"))
    assert outcome(run(ADS, page)) == ("pass", "firing")
    site = SiteConfig(url=SITE_URL, expected_google_ads_ids=["123"])  # pasted without AW-
    assert outcome(run(ADS, page, site)) == ("pass", "firing")


def test_ads_repeated_hits_are_fine() -> None:
    page = capture(gtag("AW-123"), ads_hit("123"), ads_hit("123"))
    assert outcome(run(ADS, page)) == ("pass", "firing")


def test_ads_installed_not_firing_and_wrong_id() -> None:
    assert outcome(run(ADS, capture(gtag("AW-123")))) == ("fail", "installed_not_firing")
    site = SiteConfig(url=SITE_URL, expected_google_ads_ids=["AW-999"])
    assert outcome(run(ADS, capture(gtag("AW-123"), ads_hit("123")), site)) == ("fail", "wrong_id")


def test_ads_loaded_as_a_destination_but_silent_is_installed_not_firing() -> None:
    page = capture(gtm("GTM-1"), req("https://www.googletagmanager.com/gtag/destination?id=AW-123"))
    assert outcome(run(ADS, page)) == ("fail", "installed_not_firing")


def test_ads_not_installed() -> None:
    assert outcome(run(ADS, capture(gtag("G-ABC"), ga4_hit("G-ABC")))) == ("info", "not_installed")


# -- GTM ------------------------------------------------------------------------------------------

GTM = GoogleTagManagerCheck()


def test_gtm_outcomes() -> None:
    assert outcome(run(GTM, capture(gtm("GTM-1")))) == ("pass", "loaded")
    assert outcome(run(GTM, capture(gtm("GTM-1", status=404)))) == ("fail", "script_blocked")
    assert outcome(run(GTM, capture())) == ("info", "not_installed")


# -- Speed -----------------------------------------------------------------------------------------

SPEED = PageSpeedCheck()


@pytest.mark.parametrize(
    ("lcp_ms", "expected"),
    [
        (800, ("pass", "fast")),
        (2500, ("pass", "fast")),  # the threshold itself is still "good"
        (2501, ("warn", "needs_improvement")),
        (4000, ("warn", "needs_improvement")),
        (4001, ("fail", "slow")),
        (None, ("error", "no_lcp")),
    ],
)
def test_speed_thresholds(lcp_ms: float | None, expected: tuple[Status, str]) -> None:
    assert outcome(run(SPEED, capture(lcp_ms=lcp_ms))) == expected


def test_speed_says_under_a_tenth_of_a_second_for_very_fast_pages() -> None:
    summary = run(SPEED, capture(lcp_ms=32)).summary
    assert summary == "The main content appears in under 0.1 s on mobile (lab measurement)."


def test_speed_labels_the_measurement() -> None:
    result = run(SPEED, capture(lcp_ms=3200, throttling="slow4g"))
    assert result.summary == (
        "The main content appears after 3.2 s on mobile (lab measurement, slow4g throttling)."
    )
    assert result.details["measurement"] == "lab"


# -- Mobile layout --------------------------------------------------------------------------------

LAYOUT = MobileRenderCheck()


def test_layout_outcomes() -> None:
    assert outcome(run(LAYOUT, capture())) == ("pass", "ok")
    no_viewport = capture(dom_facts=dom(viewport_meta=None))
    assert outcome(run(LAYOUT, no_viewport)) == ("fail", "missing_viewport")
    wide = capture(dom_facts=dom(scroll_width=1200, inner_width=1200))
    assert outcome(run(LAYOUT, wide)) == ("warn", "horizontal_overflow")


def test_layout_overflow_tolerates_rounding() -> None:
    assert outcome(run(LAYOUT, capture(dom_facts=dom(scroll_width=414))))[1] == "ok"
    assert outcome(run(LAYOUT, capture(dom_facts=dom(scroll_width=415))))[1] == (
        "horizontal_overflow"
    )


# -- Page health -----------------------------------------------------------------------

HEALTH = PageHealthCheck()


def test_health_ok() -> None:
    assert outcome(run(HEALTH, capture())) == ("pass", "ok")


@pytest.mark.parametrize(
    "error_code", ["timeout", "dns_failure", "connection_failed", "tls_error", "ssrf_blocked"]
)
def test_health_navigation_failures(error_code: str) -> None:
    result = run(HEALTH, capture(status=None, error_code=error_code))
    assert outcome(result) == ("fail", "navigation_failed")
    assert result.details["error_code"] == error_code


def test_health_http_error() -> None:
    assert outcome(run(HEALTH, capture(status=503))) == ("fail", "http_error")


def test_health_not_https() -> None:
    page = capture(url="http://shop.example.com/")
    assert outcome(run(HEALTH, page, SiteConfig(url="http://shop.example.com/")))[1] == "not_https"


def test_health_same_site_subdomain_redirect_is_fine() -> None:
    page = capture(final_url="https://www.example.com/landing", redirect_chain=[SITE_URL])
    assert outcome(run(HEALTH, page)) == ("pass", "ok")


def test_health_cross_domain_redirect() -> None:
    page = capture(final_url="https://parked-domains.example.net/", redirect_chain=[SITE_URL])
    result = run(HEALTH, page)
    assert outcome(result) == ("warn", "cross_domain_redirect")
    assert "example.net instead of example.com" in result.summary


def test_health_too_many_redirects() -> None:
    chain = [SITE_URL, SITE_URL + "a", SITE_URL + "b"]
    assert outcome(run(HEALTH, capture(redirect_chain=chain))) == ("warn", "too_many_redirects")
    assert outcome(run(HEALTH, capture(redirect_chain=chain[:2])))[1] == "ok"


def test_health_js_errors() -> None:
    result = run(HEALTH, capture(page_errors=["ReferenceError: x is not defined"]))
    assert outcome(result) == ("warn", "js_errors")
    assert result.details["top_errors"] == ["ReferenceError: x is not defined"]


def test_health_reports_the_worst_finding_and_counts_the_rest() -> None:
    page = capture(status=500, page_errors=["boom"], redirect_chain=[SITE_URL] * 3)
    result = run(HEALTH, page)
    assert outcome(result) == ("fail", "http_error")
    assert result.summary.endswith("(+2 more issues)")
    codes = [f["code"] for f in result.details["findings"]]
    assert codes == ["http_error", "too_many_redirects", "js_errors"]


# -- Registry, severity and explanations -------------------------------------------


def test_worst_status_order() -> None:
    assert worst_status(["pass", "fail", "warn"]) == "fail"
    assert worst_status(["pass", "warn", "info"]) == "warn"
    assert worst_status(["pass", "info"]) == "info"
    assert worst_status(["error", "pass"]) == "pass"  # "couldn't evaluate" never hides a result
    assert worst_status(["error"]) == "error"
    assert worst_status([]) == "error"


def test_run_checks_picks_checks_by_device() -> None:
    mobile = {r.check_key for r in run_checks(capture(device="mobile"), SITE)}
    desktop = {r.check_key for r in run_checks(capture(device="desktop"), SITE)}
    assert mobile == {c.check_key for c in CAPTURE_CHECKS}  # message match needs a verdict
    assert desktop == mobile - {"page_speed", "mobile_render"}


def test_a_crashing_check_becomes_an_error_result(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*args: object) -> CheckResult:
        raise RuntimeError("bug")

    monkeypatch.setattr(MetaPixelCheck, "analyze", explode)
    results = {r.check_key: r for r in run_checks(capture(), SITE)}
    assert outcome(results["meta_pixel"]) == ("error", "check_crashed")
    assert outcome(results["page_health"]) == ("pass", "ok")  # the others still ran


def test_every_code_has_an_explanation() -> None:
    for check in ALL_CHECKS:
        for code in check.codes | SHARED_CODES:
            assert explain(check.check_key, code) is not None, (check.check_key, code)
    declared = {(c.check_key, code) for c in ALL_CHECKS for code in c.codes}
    stale = {key for key in EXPLANATIONS if key[0] != "*"} - declared
    assert not stale, f"explanations for codes no check returns: {stale}"


def test_check_explanations_doc_is_up_to_date() -> None:
    from pathlib import Path

    doc = Path(__file__).resolve().parents[2] / "docs" / "check-explanations.md"
    assert doc.read_text() == render_markdown(), (
        "run: python -m tagmonitor.checks.explanations > docs/check-explanations.md"
    )
