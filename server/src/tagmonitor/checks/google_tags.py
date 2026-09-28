"""Google tags: GA4, Google Ads and Google Tag Manager (PRD §10, GoogleTagCheck).

The PRD asks for one Google check with three sub-results. Each sub-result needs its own
alert state ("your GA4 tag stopped firing" is more useful than "something Google broke"), so
they are three checks with three check keys that share the parsers in tracking_patterns.py.
The dashboard's single "Google" dot shows the worst of the three.

A GT- id (the newer "Google tag") can route to GA4 and/or Ads destinations we can't see, so
it counts as neither: its hits are still attributed by the ids they carry.
"""

from collections import Counter

from tagmonitor.checks.base import Check, CheckResult, SiteConfig
from tagmonitor.checks.tag_verdict import TagObservation, loaded_ok, tag_verdict, was_sent
from tagmonitor.checks.tracking_patterns import (
    normalize_ads_id,
    parse_ads_hit,
    parse_ga4_hits,
    parse_google_script,
)
from tagmonitor.page_capture import PageCapture


class GoogleAnalyticsCheck(Check):
    check_key = "google_ga4"
    title = "Google Analytics 4"
    codes = frozenset(
        {
            "firing",
            "duplicate_pageview",
            "installed_not_firing",
            "script_blocked",
            "not_installed",
            "wrong_id",
        }
    )

    def analyze(self, capture: PageCapture, site: SiteConfig) -> CheckResult:
        if not capture.navigation.document_loaded:
            return self.not_evaluated(capture)
        scripts = []
        seen_ids: set[str] = set()
        events: Counter[tuple[str, str]] = Counter()
        for request in capture.requests:
            script = parse_google_script(request.url)
            if script and script.kind == "gtag" and script.tag_id.startswith("G-"):
                scripts.append(request)
                seen_ids.add(script.tag_id)
            if was_sent(request):
                for hit in parse_ga4_hits(request):
                    events[(hit.measurement_id, hit.event)] += 1
                    seen_ids.add(hit.measurement_id)
        return tag_verdict(
            self,
            TagObservation(scripts=scripts, events=events, seen_ids=seen_ids),
            tag_name="GA4",
            expected_ids=[i.strip().upper() for i in site.expected_ga4_ids if i.strip()],
            page_view_event="page_view",
            wrong_id_code="wrong_id",
        )


class GoogleAdsCheck(Check):
    check_key = "google_ads"
    title = "Google Ads"
    codes = frozenset(
        {"firing", "installed_not_firing", "script_blocked", "not_installed", "wrong_id"}
    )

    def analyze(self, capture: PageCapture, site: SiteConfig) -> CheckResult:
        if not capture.navigation.document_loaded:
            return self.not_evaluated(capture)
        scripts = []
        seen_ids: set[str] = set()
        events: Counter[tuple[str, str]] = Counter()
        for request in capture.requests:
            script = parse_google_script(request.url)
            if script and script.kind == "gtag" and script.tag_id.startswith("AW-"):
                scripts.append(request)
                seen_ids.add(script.tag_id)
            hit = parse_ads_hit(request.url)
            if hit is not None and was_sent(request):
                events[(hit.conversion_id, hit.kind)] += 1
                seen_ids.add(hit.conversion_id)
        return tag_verdict(
            self,
            TagObservation(scripts=scripts, events=events, seen_ids=seen_ids),
            tag_name="Google Ads",
            expected_ids=[normalize_ads_id(i) for i in site.expected_google_ads_ids if i.strip()],
            # Conversions legitimately repeat, so any hit for the id counts as firing.
            page_view_event=None,
            wrong_id_code="wrong_id",
        )


class GoogleTagManagerCheck(Check):
    check_key = "google_gtm"
    title = "Google Tag Manager"
    codes = frozenset({"loaded", "script_blocked", "not_installed"})

    def analyze(self, capture: PageCapture, site: SiteConfig) -> CheckResult:
        if not capture.navigation.document_loaded:
            return self.not_evaluated(capture)
        containers = []
        for request in capture.requests:
            script = parse_google_script(request.url)
            if script and script.kind == "gtm":
                containers.append((script.tag_id, request))
        if not containers:
            return self.result(
                "info", "not_installed", "No Google Tag Manager container on this page."
            )
        loaded = sorted({tag_id for tag_id, request in containers if loaded_ok(request)})
        failed = sorted({tag_id for tag_id, request in containers if not loaded_ok(request)})
        if not loaded:
            return self.result(
                "fail",
                "script_blocked",
                f"The Tag Manager container {', '.join(failed)} failed to load, so none of "
                "the tags inside it can run.",
                ids=failed,
            )
        return self.result(
            "pass",
            "loaded",
            f"Tag Manager container {', '.join(loaded)} loaded.",
            ids=loaded,
            failed_ids=failed,
        )
