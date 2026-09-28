"""Unit tests for the tracking endpoint patterns. URLs mirror what the real tags send."""

import pytest

from tagmonitor.checks.tracking_patterns import (
    AdsHit,
    Ga4Hit,
    GoogleScript,
    MetaHit,
    classify,
    is_meta_script,
    is_tag_host,
    meta_config_pixel_id,
    normalize_ads_id,
    parse_ads_hit,
    parse_ga4_hits,
    parse_google_script,
    parse_meta_hit,
)
from tagmonitor.page_capture import NetworkRequest


def request(url: str, method: str = "GET", post_data: str | None = None) -> NetworkRequest:
    return NetworkRequest(
        url=url, method=method, resource_type="other", started_ms=0, post_data=post_data
    )


# -- Meta ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://connect.facebook.net/en_US/fbevents.js",
        "https://connect.facebook.net/de_DE/fbevents.js",
        "https://connect.facebook.net/en_US/fbevents.js?v=next",
    ],
)
def test_meta_script(url: str) -> None:
    assert is_meta_script(url)


def test_meta_script_ignores_other_facebook_scripts() -> None:
    assert not is_meta_script("https://connect.facebook.net/en_US/sdk.js")


def test_meta_config_request_reveals_the_pixel_id() -> None:
    url = "https://connect.facebook.net/signals/config/123456789012345?v=2.9.170&r=stable"
    assert meta_config_pixel_id(url) == "123456789012345"
    assert meta_config_pixel_id("https://connect.facebook.net/signals/plugins/foo.js") is None


def test_meta_get_hit() -> None:
    url = "https://www.facebook.com/tr/?id=123&ev=PageView&dl=https%3A%2F%2Fshop.example&ts=1"
    assert parse_meta_hit(request(url)) == MetaHit("123", "PageView")


def test_meta_hit_without_trailing_slash_or_www() -> None:
    assert parse_meta_hit(request("https://facebook.com/tr?id=9&ev=Lead")) == MetaHit("9", "Lead")


def test_meta_urlencoded_post_hit() -> None:
    hit = parse_meta_hit(
        request("https://www.facebook.com/tr/", "POST", "id=555&ev=Purchase&cd%5Bvalue%5D=19.99")
    )
    assert hit == MetaHit("555", "Purchase")


def test_meta_multipart_post_hit() -> None:
    body = (
        "------WebKitFormBoundaryX\r\n"
        'Content-Disposition: form-data; name="id"\r\n\r\n777\r\n'
        "------WebKitFormBoundaryX\r\n"
        'Content-Disposition: form-data; name="ev"\r\n\r\nPageView\r\n'
        "------WebKitFormBoundaryX--\r\n"
    )
    hit = parse_meta_hit(request("https://www.facebook.com/tr/", "POST", body))
    assert hit == MetaHit("777", "PageView")


@pytest.mark.parametrize(
    "url",
    [
        "https://www.facebook.com/tr/?ev=PageView",  # no pixel id
        "https://www.facebook.com/tracking/?id=1&ev=PageView",  # different path
        "https://www.notfacebook.com/tr/?id=1&ev=PageView",  # lookalike host
    ],
)
def test_not_meta_hits(url: str) -> None:
    assert parse_meta_hit(request(url)) is None


# -- Google ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://www.googletagmanager.com/gtm.js?id=GTM-ABC123",
            GoogleScript("gtm", "GTM-ABC123"),
        ),
        (
            "https://www.googletagmanager.com/gtm.js?id=GTM-ABC123&l=dl",
            GoogleScript("gtm", "GTM-ABC123"),
        ),
        ("https://www.googletagmanager.com/gtag/js?id=G-XYZ789", GoogleScript("gtag", "G-XYZ789")),
        (
            "https://www.googletagmanager.com/gtag/js?id=AW-123456",
            GoogleScript("gtag", "AW-123456"),
        ),
        ("https://www.googletagmanager.com/gtag/js?id=GT-KDEF", GoogleScript("gtag", "GT-KDEF")),
        ("https://www.googletagmanager.com/gtag/js?id=g-lower1", GoogleScript("gtag", "G-LOWER1")),
    ],
)
def test_google_scripts(url: str, expected: GoogleScript) -> None:
    assert parse_google_script(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://www.googletagmanager.com/gtm.js",  # no id
        "https://www.googletagmanager.com/gtag/js?id=UA-1-1",  # retired Universal Analytics
        "https://www.googletagmanager.com/ns.html?id=GTM-ABC123",  # noscript iframe
    ],
)
def test_not_google_scripts(url: str) -> None:
    assert parse_google_script(url) is None


def test_ga4_single_event_in_query() -> None:
    url = "https://region1.google-analytics.com/g/collect?v=2&tid=G-ABC&cid=1.2&en=page_view"
    assert parse_ga4_hits(request(url, "POST")) == [Ga4Hit("G-ABC", "page_view")]


def test_ga4_on_analytics_google_com() -> None:
    url = "https://analytics.google.com/g/collect?v=2&tid=G-ABC&en=page_view"
    assert parse_ga4_hits(request(url)) == [Ga4Hit("G-ABC", "page_view")]


def test_ga4_batched_events_in_post_body() -> None:
    url = "https://region1.google-analytics.com/g/collect?v=2&tid=G-ABC&cid=1.2"
    body = "en=page_view&_et=1\r\nen=scroll&epn.percent_scrolled=90\r\nen=user_engagement"
    hits = parse_ga4_hits(request(url, "POST", body))
    assert hits == [
        Ga4Hit("G-ABC", "page_view"),
        Ga4Hit("G-ABC", "scroll"),
        Ga4Hit("G-ABC", "user_engagement"),
    ]


def test_not_ga4_hits() -> None:
    assert parse_ga4_hits(request("https://www.google-analytics.com/collect?v=1&tid=UA-1")) == []
    assert parse_ga4_hits(request("https://evil.example/g/collect?tid=G-ABC&en=page_view")) == []


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://googleads.g.doubleclick.net/pagead/viewthroughconversion/123456789/?random=1",
            AdsHit("AW-123456789", "remarketing"),
        ),
        (
            "https://www.googleadservices.com/pagead/conversion/123456789/?label=AbC&value=10",
            AdsHit("AW-123456789", "conversion"),
        ),
    ],
)
def test_ads_hits(url: str, expected: AdsHit) -> None:
    assert parse_ads_hit(url) == expected


def test_not_ads_hits() -> None:
    assert parse_ads_hit("https://googleads.g.doubleclick.net/pagead/id") is None
    assert parse_ads_hit("https://www.googleadservices.com/pagead/conversion_async.js") is None


@pytest.mark.parametrize("value", ["AW-123", "aw-123", " 123 ", "AW-123"])
def test_normalize_ads_id(value: str) -> None:
    assert normalize_ads_id(value) == "AW-123"


def test_tag_hosts_and_classification() -> None:
    assert is_tag_host("https://stats.g.doubleclick.net/g/collect")
    assert not is_tag_host("https://cdn.shop.example/app.js")
    assert classify(request("https://www.facebook.com/tr/?id=1&ev=PageView")) == "meta hit"
    assert classify(request("https://www.facebook.com/privacy_sandbox/pixel/register")) is None
