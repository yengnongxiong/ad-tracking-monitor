"""How fast does the main content appear on mobile? (PRD §10)"""

from tagmonitor.checks.base import Check, CheckResult, SiteConfig
from tagmonitor.page_capture import PageCapture

# Core Web Vitals thresholds for Largest Contentful Paint (web.dev/articles/lcp).
GOOD_LCP_MS = 2500
POOR_LCP_MS = 4000


class PageSpeedCheck(Check):
    check_key = "page_speed"
    title = "Mobile speed"
    devices = frozenset({"mobile"})
    codes = frozenset({"fast", "needs_improvement", "slow", "no_lcp"})

    def analyze(self, capture: PageCapture, site: SiteConfig) -> CheckResult:
        if not capture.navigation.document_loaded:
            return self.not_evaluated(capture)
        performance = capture.performance
        # Always say how we measured: one lab page load, not real visitors' phones.
        measurement = "lab measurement" + (
            "" if capture.throttling == "none" else f", {capture.throttling} throttling"
        )
        details = {
            "lcp_ms": performance.lcp_ms,
            "load_ms": performance.load_ms,
            "transfer_bytes": performance.transfer_bytes,
            "request_count": performance.request_count,
            "measurement": "lab",
            "throttling": capture.throttling,
        }
        if performance.lcp_ms is None:
            return self.result(
                "error",
                "no_lcp",
                "We couldn't measure how fast the main content appears.",
                **details,
            )
        # Rounding a 30 ms paint to "0.0 s" reads like a bug, so very fast pages say so.
        timing = (
            "in under 0.1 s"
            if performance.lcp_ms < 100
            else f"after {performance.lcp_ms / 1000:.1f} s"
        )
        sentence = f"The main content appears {timing} on mobile ({measurement})."
        if performance.lcp_ms <= GOOD_LCP_MS:
            return self.result("pass", "fast", sentence, **details)
        if performance.lcp_ms <= POOR_LCP_MS:
            return self.result("warn", "needs_improvement", sentence, **details)
        return self.result("fail", "slow", sentence, **details)
