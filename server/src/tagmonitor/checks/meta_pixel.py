"""Is the Meta Pixel installed and firing PageView? (PRD §10)"""

from collections import Counter

from tagmonitor.checks.base import Check, CheckResult, SiteConfig
from tagmonitor.checks.tag_verdict import TagObservation, tag_verdict, was_sent
from tagmonitor.checks.tracking_patterns import is_meta_script, meta_config_pixel_id, parse_meta_hit
from tagmonitor.page_capture import PageCapture


class MetaPixelCheck(Check):
    check_key = "meta_pixel"
    title = "Meta Pixel"
    codes = frozenset(
        {
            "firing",
            "duplicate_pageview",
            "installed_not_firing",
            "script_blocked",
            "not_installed",
            "wrong_pixel",
        }
    )

    def analyze(self, capture: PageCapture, site: SiteConfig) -> CheckResult:
        if not capture.navigation.document_loaded:
            return self.not_evaluated(capture)

        events: Counter[tuple[str, str]] = Counter()
        seen_ids: set[str] = set()
        for request in capture.requests:
            if (pixel_id := meta_config_pixel_id(request.url)) is not None:
                seen_ids.add(pixel_id)
            hit = parse_meta_hit(request)
            if hit is not None and was_sent(request):
                events[(hit.pixel_id, hit.event)] += 1
                seen_ids.add(hit.pixel_id)

        observation = TagObservation(
            scripts=[r for r in capture.requests if is_meta_script(r.url)],
            events=events,
            seen_ids=seen_ids,
        )
        return tag_verdict(
            self,
            observation,
            tag_name="Meta Pixel",
            expected_ids=[i.strip() for i in site.expected_meta_pixel_ids if i.strip()],
            page_view_event="PageView",
            wrong_id_code="wrong_pixel",
        )
