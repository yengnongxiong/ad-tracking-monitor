"""What every check should say about every fixture site (PRD §14 + §10), in one table.

Used twice: end to end with a real browser (test_fixture_checks.py) and against saved
captures without a browser (test_saved_captures.py).

Fixture sites are served over plain http://, so Page health correctly fails them all for
"not_https"; the other Page health outcomes are covered in test_checks.py with https pages.
"""

from tagmonitor.checks.base import SiteConfig, Status

# Checks not listed for a site: the tag checks default to "not_installed" (info), speed to
# "fast", layout to "ok", and page health to "not_https".
DEFAULTS: dict[str, tuple[Status, str]] = {
    "meta_pixel": ("info", "not_installed"),
    "google_ga4": ("info", "not_installed"),
    "google_ads": ("info", "not_installed"),
    "google_gtm": ("info", "not_installed"),
    "page_speed": ("pass", "fast"),
    "mobile_render": ("pass", "ok"),
    "page_health": ("fail", "not_https"),
}

NOT_EVALUATED: tuple[Status, str] = ("error", "not_evaluated")

EXPECTED: dict[str, dict[str, tuple[Status, str]]] = {
    "meta_ok": {"meta_pixel": ("pass", "firing")},
    "meta_not_firing": {"meta_pixel": ("fail", "installed_not_firing")},
    "meta_duplicate_pageview": {"meta_pixel": ("warn", "duplicate_pageview")},
    "meta_wrong_id": {"meta_pixel": ("fail", "wrong_pixel")},
    "ga4_ok": {"google_ga4": ("pass", "firing")},
    "gtm_ga4_ok": {"google_ga4": ("pass", "firing"), "google_gtm": ("pass", "loaded")},
    "google_ads_ok": {"google_ads": ("pass", "firing")},
    "no_tags": {},
    "slow_lcp": {"page_speed": ("fail", "slow")},
    "mobile_overflow": {"mobile_render": ("warn", "horizontal_overflow")},
    "no_viewport": {"mobile_render": ("fail", "missing_viewport")},
    "http_500": {"page_health": ("fail", "http_error")},
    "redirect_chain_3": {},  # not_https, plus a too_many_redirects finding (asserted separately)
    "js_error": {},  # not_https, plus a js_errors finding (asserted separately)
    "redirect_to_private_ip": {
        "meta_pixel": NOT_EVALUATED,
        "google_ga4": NOT_EVALUATED,
        "google_ads": NOT_EVALUATED,
        "google_gtm": NOT_EVALUATED,
        "page_speed": NOT_EVALUATED,
        "mobile_render": NOT_EVALUATED,
        "page_health": ("fail", "navigation_failed"),
    },
}

# Extra Page health findings that sit behind the headline "not_https".
EXTRA_HEALTH_FINDINGS = {
    "redirect_chain_3": "too_many_redirects",
    "js_error": "js_errors",
}


def site_config(site: str, url: str) -> SiteConfig:
    """meta_wrong_id is only "wrong" because the owner told us to expect another pixel."""
    if site == "meta_wrong_id":
        return SiteConfig(url=url, expected_meta_pixel_ids=["111111111111111"])
    return SiteConfig(url=url)


def expected_for(site: str) -> dict[str, tuple[Status, str]]:
    return DEFAULTS | EXPECTED[site]
