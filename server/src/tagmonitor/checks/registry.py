"""The one list of checks. Adding a check = write the class, add it here, explain its codes."""

import logging
from collections.abc import Callable
from functools import partial

from tagmonitor.checks.base import Check, CheckBase, CheckResult, SiteConfig
from tagmonitor.checks.google_tags import (
    GoogleAdsCheck,
    GoogleAnalyticsCheck,
    GoogleTagManagerCheck,
)
from tagmonitor.checks.message_match import MessageMatchCheck, MessageMatchOutcome
from tagmonitor.checks.meta_pixel import MetaPixelCheck
from tagmonitor.checks.mobile_render import MobileRenderCheck
from tagmonitor.checks.page_health import PageHealthCheck
from tagmonitor.checks.page_speed import PageSpeedCheck
from tagmonitor.page_capture import PageCapture

log = logging.getLogger(__name__)

# Checks computed from the page capture alone.
CAPTURE_CHECKS: tuple[Check, ...] = (
    MetaPixelCheck(),
    GoogleAnalyticsCheck(),
    GoogleAdsCheck(),
    GoogleTagManagerCheck(),
    PageSpeedCheck(),
    MobileRenderCheck(),
    PageHealthCheck(),
)
# Needs the language model's verdict too, so it only runs when the worker passes one in
# (sites with ad copy). ADR-017.
MESSAGE_MATCH = MessageMatchCheck()

# Every check, in display order: for titles, explanations and the dashboard.
ALL_CHECKS: tuple[CheckBase, ...] = (*CAPTURE_CHECKS, MESSAGE_MATCH)
CHECKS_BY_KEY = {check.check_key: check for check in ALL_CHECKS}


def run_checks(
    capture: PageCapture,
    site: SiteConfig,
    message_match: MessageMatchOutcome | None = None,
) -> list[CheckResult]:
    """Run every check that applies to the capture's device.

    `message_match` is the language model's verdict on this page (computed by the worker
    beforehand). Without one, the message match check doesn't run.

    A bug in one check must not cost us the others, so a crash becomes an "error" result
    (which never alerts) and a log entry.
    """
    results = []
    for check in CAPTURE_CHECKS:
        if capture.device in check.devices:
            results.append(_guarded(check, capture, partial(check.analyze, capture, site)))
    if message_match is not None and capture.device in MESSAGE_MATCH.devices:
        analyze = partial(MESSAGE_MATCH.analyze, capture, message_match)
        results.append(_guarded(MESSAGE_MATCH, capture, analyze))
    return results


def _guarded(
    check: CheckBase, capture: PageCapture, analyze: Callable[[], CheckResult]
) -> CheckResult:
    try:
        return analyze()
    except Exception:
        log.exception("check %s crashed on %s", check.check_key, capture.url)
        return check.result("error", "check_crashed", "This check hit an internal error.")
