"""The one list of checks. Adding a check = write the class, add it here, explain its codes."""

import logging

from tagmonitor.checks.base import Check, CheckResult, SiteConfig
from tagmonitor.checks.google_tags import (
    GoogleAdsCheck,
    GoogleAnalyticsCheck,
    GoogleTagManagerCheck,
)
from tagmonitor.checks.meta_pixel import MetaPixelCheck
from tagmonitor.checks.mobile_render import MobileRenderCheck
from tagmonitor.checks.page_health import PageHealthCheck
from tagmonitor.checks.page_speed import PageSpeedCheck
from tagmonitor.page_capture import PageCapture

log = logging.getLogger(__name__)

ALL_CHECKS: tuple[Check, ...] = (
    MetaPixelCheck(),
    GoogleAnalyticsCheck(),
    GoogleAdsCheck(),
    GoogleTagManagerCheck(),
    PageSpeedCheck(),
    MobileRenderCheck(),
    PageHealthCheck(),
)

CHECKS_BY_KEY = {check.check_key: check for check in ALL_CHECKS}


def run_checks(capture: PageCapture, site: SiteConfig) -> list[CheckResult]:
    """Run every check that applies to the capture's device.

    A bug in one check must not cost us the others, so a crash becomes an "error" result
    (which never alerts) and a log entry.
    """
    results = []
    for check in ALL_CHECKS:
        if capture.device not in check.devices:
            continue
        try:
            results.append(check.analyze(capture, site))
        except Exception:
            log.exception("check %s crashed on %s", check.check_key, capture.url)
            results.append(
                check.result("error", "check_crashed", "This check hit an internal error.")
            )
    return results
