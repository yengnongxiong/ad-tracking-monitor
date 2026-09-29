"""Real Chromium captures of the fixture sites (PRD §14). No request leaves the machine."""

import asyncio
from urllib.parse import parse_qs, urlsplit

import pytest

from tagmonitor.browser.capturer import PageCapturer
from tagmonitor.browser.ssrf import SsrfError
from tagmonitor.checks.tracking_patterns import is_ga4_hit, is_meta_hit
from tagmonitor.page_capture import PageCapture
from tests.conftest import TEST_POLICY, TEST_RESOLVER
from tests.fixture_server import FixtureServer


async def capture(
    capturer: PageCapturer, server: FixtureServer, site: str, device: str = "mobile"
) -> PageCapture:
    result = await capturer.capture(server.url(site), device)  # type: ignore[arg-type]
    return result.capture


def meta_hits(capture: PageCapture) -> list[tuple[str, str]]:
    hits = []
    for request in capture.requests:
        if is_meta_hit(request.url):
            query = parse_qs(urlsplit(request.url).query)
            hits.append((query["id"][0], query["ev"][0]))
    return hits


def ga4_hits(capture: PageCapture) -> list[tuple[str, str]]:
    hits = []
    for request in capture.requests:
        if is_ga4_hit(request.url):
            query = parse_qs(urlsplit(request.url).query)
            hits.append((query["tid"][0], query["en"][0]))
    return hits


def requested(capture: PageCapture, fragment: str) -> list[int | None]:
    """Statuses of requests whose URL contains `fragment`."""
    return [r.status for r in capture.requests if fragment in r.url]


async def test_meta_ok(capturer: PageCapturer, fixture_server: FixtureServer) -> None:
    result = await capture(capturer, fixture_server, "meta_ok")
    assert requested(result, "connect.facebook.net/en_US/fbevents.js") == [200]
    assert meta_hits(result) == [("111111111111111", "PageView")]


async def test_meta_not_firing(capturer: PageCapturer, fixture_server: FixtureServer) -> None:
    result = await capture(capturer, fixture_server, "meta_not_firing")
    assert requested(result, "fbevents.js") == [200]
    assert meta_hits(result) == []


async def test_meta_duplicate_pageview(
    capturer: PageCapturer, fixture_server: FixtureServer
) -> None:
    result = await capture(capturer, fixture_server, "meta_duplicate_pageview")
    assert meta_hits(result) == [("333333333333333", "PageView")] * 2


async def test_meta_wrong_id(capturer: PageCapturer, fixture_server: FixtureServer) -> None:
    result = await capture(capturer, fixture_server, "meta_wrong_id")
    assert meta_hits(result) == [("444444444444444", "PageView")]


async def test_ga4_ok(capturer: PageCapturer, fixture_server: FixtureServer) -> None:
    result = await capture(capturer, fixture_server, "ga4_ok")
    assert requested(result, "googletagmanager.com/gtag/js?id=G-TEST123") == [200]
    assert ga4_hits(result) == [("G-TEST123", "page_view")]
    # GA4 sends beacons as POSTs; the capture keeps tracking POST bodies (empty here).
    assert [r.method for r in result.requests if is_ga4_hit(r.url)] == ["POST"]


async def test_gtm_ga4_ok(capturer: PageCapturer, fixture_server: FixtureServer) -> None:
    result = await capture(capturer, fixture_server, "gtm_ga4_ok")
    assert requested(result, "googletagmanager.com/gtm.js?id=GTM-GTMTEST") == [200]
    assert ga4_hits(result) == [("G-GTMTEST", "page_view")]


async def test_google_ads_ok(capturer: PageCapturer, fixture_server: FixtureServer) -> None:
    result = await capture(capturer, fixture_server, "google_ads_ok")
    assert requested(result, "googleads.g.doubleclick.net/pagead/viewthroughconversion/123456789/")


async def test_no_tags(capturer: PageCapturer, fixture_server: FixtureServer) -> None:
    result = await capture(capturer, fixture_server, "no_tags")
    assert meta_hits(result) == [] and ga4_hits(result) == []
    assert [r.url for r in result.requests] == [fixture_server.url("no_tags")]


async def test_dom_facts(capturer: PageCapturer, fixture_server: FixtureServer) -> None:
    result = await capture(capturer, fixture_server, "no_tags")
    dom = result.dom
    assert dom is not None
    assert dom.title == "No tags at all"
    assert dom.meta_description == "Fresh coffee beans roasted to order. 20% off your first bag."
    assert dom.viewport_meta == "width=device-width, initial-scale=1"
    assert dom.h1 == ["Fresh coffee, roasted this week"]
    assert dom.h2 == ["Our story"]
    assert dom.above_fold_text.startswith("Fresh coffee, roasted this week Hand-roasted beans")
    assert dom.button_texts == ["Get 20% off your first bag", "Subscribe"]
    assert result.navigation.status == 200
    assert result.navigation.load_event_fired


async def test_slow_lcp(capturer: PageCapturer, fixture_server: FixtureServer) -> None:
    result = await capture(capturer, fixture_server, "slow_lcp")
    assert result.performance.lcp_ms is not None
    assert result.performance.lcp_ms >= 4_500  # the server holds the hero image for 5 s


async def test_fast_page_has_a_fast_lcp(
    capturer: PageCapturer, fixture_server: FixtureServer
) -> None:
    result = await capture(capturer, fixture_server, "no_tags")
    assert result.performance.lcp_ms is not None
    assert result.performance.lcp_ms < 2_500
    assert result.performance.request_count == 1
    assert result.performance.transfer_bytes > 0


async def test_mobile_overflow(capturer: PageCapturer, fixture_server: FixtureServer) -> None:
    result = await capture(capturer, fixture_server, "mobile_overflow")
    assert result.dom is not None
    assert result.dom.viewport_width == 412  # Pixel 7
    assert result.dom.scroll_width > result.dom.viewport_width + 2
    # Why the check can't compare against innerWidth: mobile Chrome widens the layout
    # viewport to fit the content, so innerWidth grows with the overflow.
    assert result.dom.inner_width == result.dom.scroll_width


async def test_no_viewport(capturer: PageCapturer, fixture_server: FixtureServer) -> None:
    result = await capture(capturer, fixture_server, "no_viewport")
    assert result.dom is not None
    assert result.dom.viewport_meta is None


async def test_http_500(capturer: PageCapturer, fixture_server: FixtureServer) -> None:
    result = await capture(capturer, fixture_server, "http_500")
    assert result.navigation.status == 500
    assert result.navigation.error_code is None  # an error page is still a loaded document
    assert result.dom is not None


async def test_redirect_chain_3(capturer: PageCapturer, fixture_server: FixtureServer) -> None:
    result = await capture(capturer, fixture_server, "redirect_chain_3")
    assert result.navigation.redirect_chain == [
        fixture_server.url("redirect_chain_3"),
        fixture_server.url("redirect_chain_3") + "hop1",
        fixture_server.url("redirect_chain_3") + "hop2",
    ]
    assert result.navigation.final_url == fixture_server.url("redirect_chain_3") + "final/"
    assert result.navigation.status == 200


async def test_js_error(capturer: PageCapturer, fixture_server: FixtureServer) -> None:
    result = await capture(capturer, fixture_server, "js_error")
    assert any("initCheckoutWidget is not defined" in e for e in result.page_errors)
    assert "checkout widget failed to load" in result.console_errors


async def test_redirect_to_private_ip_is_blocked(
    capturer: PageCapturer, fixture_server: FixtureServer
) -> None:
    fixture_server.hits.clear()
    result = await capture(capturer, fixture_server, "redirect_to_private_ip")

    assert result.navigation.error_code == "ssrf_blocked"
    assert result.navigation.status is None
    assert result.dom is None
    assert any(b.host == "127.0.0.1" and b.code == "ssrf_blocked" for b in result.blocked)
    assert "/internal/secret" not in fixture_server.hits  # the browser never reached it


async def test_desktop_profile(capturer: PageCapturer, fixture_server: FixtureServer) -> None:
    result = await capture(capturer, fixture_server, "mobile_overflow", device="desktop")
    assert result.dom is not None
    assert result.dom.viewport_width == result.dom.inner_width == 1440
    assert "Mobile" not in result.user_agent
    assert result.dom.scroll_width <= result.dom.viewport_width  # 1200px fits on desktop


async def test_screenshot_and_json_round_trip(
    capturer: PageCapturer, fixture_server: FixtureServer
) -> None:
    result = await capturer.capture(fixture_server.url("meta_ok"), "mobile")
    assert result.screenshot_jpeg is not None
    assert result.screenshot_jpeg[:2] == b"\xff\xd8"  # JPEG magic bytes
    restored = PageCapture.model_validate_json(result.capture.model_dump_json())
    assert restored == result.capture


async def test_rejects_invalid_urls_before_using_the_browser(capturer: PageCapturer) -> None:
    with pytest.raises(SsrfError) as info:
        await capturer.capture("ftp://fixtures.test/", "mobile")
    assert info.value.code == "invalid_url"


async def test_unknown_hosts_fail_without_touching_the_internet(capturer: PageCapturer) -> None:
    result = (await capturer.capture("https://landing.example/", "mobile")).capture
    assert result.navigation.error_code == "dns_failure"


async def test_relaunches_the_browser_after_n_captures(fixture_server: FixtureServer) -> None:
    async with PageCapturer(
        policy=TEST_POLICY, resolver=TEST_RESOLVER, relaunch_after=2
    ) as capturer:
        for _ in range(3):
            await capturer.capture(fixture_server.url("no_tags"), "desktop")
        assert capturer.browser_launches == 2


async def test_slow4g_throttling_slows_the_page(fixture_server: FixtureServer) -> None:
    async with PageCapturer(
        policy=TEST_POLICY, resolver=TEST_RESOLVER, throttling="slow4g"
    ) as capturer:
        result = (await capturer.capture(fixture_server.url("no_tags"), "mobile")).capture
    assert result.throttling == "slow4g"
    assert result.performance.load_ms is not None
    assert result.performance.load_ms > 500  # one round trip alone costs 562.5 ms


async def test_browser_relaunch_waits_for_captures_still_using_it(
    fixture_server: FixtureServer,
) -> None:
    """With concurrent captures, replacing the browser must not kill the ones in flight."""
    async with PageCapturer(
        policy=TEST_POLICY, resolver=TEST_RESOLVER, relaunch_after=2
    ) as capturer:
        results = await asyncio.gather(
            *(capturer.capture(fixture_server.url("slow_lcp"), "mobile") for _ in range(5))
        )
        assert all(r.capture.navigation.status == 200 for r in results)
        assert capturer.browser_launches >= 3


def test_tracking_stubs_only_apply_to_allowlisted_dev_hosts() -> None:
    capturer = PageCapturer(policy=TEST_POLICY, resolver=TEST_RESOLVER, tracking_stubs=True)
    assert capturer.uses_tracking_stubs("http://fixtures.test:8000/meta_ok/")
    assert not capturer.uses_tracking_stubs("https://real-shop.example/")
    off = PageCapturer(policy=TEST_POLICY, resolver=TEST_RESOLVER, tracking_stubs=False)
    assert not off.uses_tracking_stubs("http://fixtures.test:8000/meta_ok/")
