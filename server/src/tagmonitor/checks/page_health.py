"""Does the landing page load cleanly? (PRD §10)

Unlike the other checks, this one also reports on pages that didn't load at all: that *is*
its job. When a page is down, the other checks return "not evaluated", so the owner gets one
clear "your page is down" alert instead of one alert per check.
"""

from typing import Any
from urllib.parse import urlsplit

from tagmonitor.checks.base import Check, CheckResult, SiteConfig, Status, worst_status
from tagmonitor.page_capture import PageCapture
from tagmonitor.urls import registrable_domain

MAX_REDIRECTS = 2

NAVIGATION_ERRORS = {
    "timeout": "it took longer than 30 seconds to respond",
    "dns_failure": "its domain name doesn't resolve",
    "connection_failed": "its server refused or dropped the connection",
    "tls_error": "its HTTPS certificate isn't valid",
    "ssrf_blocked": "it points to a private network address",
    "navigation_failed": "the browser couldn't open it",
}


class PageHealthCheck(Check):
    check_key = "page_health"
    title = "Page health"
    codes = frozenset(
        {
            "ok",
            "navigation_failed",
            "http_error",
            "not_https",
            "cross_domain_redirect",
            "too_many_redirects",
            "js_errors",
        }
    )

    def analyze(self, capture: PageCapture, site: SiteConfig) -> CheckResult:
        navigation = capture.navigation
        if navigation.error_code is not None:
            reason = NAVIGATION_ERRORS.get(navigation.error_code, "the browser couldn't open it")
            return self.result(
                "fail",
                "navigation_failed",
                f"The page didn't load: {reason}.",
                error_code=navigation.error_code,
                error=navigation.error,
            )

        findings: list[tuple[Status, str, str]] = []  # (status, code, sentence)
        if navigation.status is not None and navigation.status >= 400:
            findings.append(
                ("fail", "http_error", f"The page returns an error (HTTP {navigation.status}).")
            )
        final_url = navigation.final_url or capture.url
        if urlsplit(final_url).scheme != "https":
            findings.append(
                ("fail", "not_https", "The page isn't served over a secure (HTTPS) connection.")
            )
        landed_on, expected = registrable_domain(final_url), registrable_domain(site.url)
        if landed_on != expected:
            findings.append(
                (
                    "warn",
                    "cross_domain_redirect",
                    f"Visitors end up on {landed_on} instead of {expected}.",
                )
            )
        if len(navigation.redirect_chain) > MAX_REDIRECTS:
            findings.append(
                (
                    "warn",
                    "too_many_redirects",
                    f"The page goes through {len(navigation.redirect_chain)} redirects before "
                    "it loads.",
                )
            )
        errors = capture.page_errors + capture.console_errors
        if errors:
            findings.append(
                ("warn", "js_errors", f"The page reports {len(errors)} JavaScript error(s).")
            )

        details: dict[str, Any] = {
            "http_status": navigation.status,
            "final_url": final_url,
            "redirect_chain": navigation.redirect_chain,
            "top_errors": errors[:5],
            "findings": [{"status": s, "code": c, "summary": m} for s, c, m in findings],
        }
        if not findings:
            return self.result("pass", "ok", "The page loads cleanly over HTTPS.", **details)
        worst = worst_status(status for status, _, _ in findings)
        status, code, sentence = next(f for f in findings if f[0] == worst)
        if len(findings) > 1:
            sentence += f" (+{len(findings) - 1} more issue{'s' if len(findings) > 2 else ''})"
        return self.result(status, code, sentence, **details)
