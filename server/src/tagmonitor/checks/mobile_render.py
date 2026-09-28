"""Does the page render properly on a phone? (PRD §10)"""

from tagmonitor.checks.base import Check, CheckResult, SiteConfig
from tagmonitor.page_capture import PageCapture

# Sub-pixel rounding can make scrollWidth a pixel or two wider than the viewport.
OVERFLOW_TOLERANCE_PX = 2


class MobileRenderCheck(Check):
    check_key = "mobile_render"
    title = "Mobile layout"
    devices = frozenset({"mobile"})
    codes = frozenset({"ok", "missing_viewport", "horizontal_overflow"})

    def analyze(self, capture: PageCapture, site: SiteConfig) -> CheckResult:
        dom = capture.dom
        if not capture.navigation.document_loaded or dom is None:
            return self.not_evaluated(capture)
        details = {
            "viewport_meta": dom.viewport_meta,
            "scroll_width": dom.scroll_width,
            "viewport_width": dom.viewport_width,
            "screenshot_key": capture.screenshot_key,
        }
        if not dom.viewport_meta:
            return self.result(
                "fail",
                "missing_viewport",
                "The page has no mobile viewport tag, so phones show a shrunken desktop page.",
                **details,
            )
        # Compared with the device width, not innerWidth: mobile Chrome widens innerWidth to
        # fit overflowing content (docs/milestones/M2.md).
        if dom.scroll_width > dom.viewport_width + OVERFLOW_TOLERANCE_PX:
            return self.result(
                "warn",
                "horizontal_overflow",
                f"The page is {dom.scroll_width}px wide on a {dom.viewport_width}px phone "
                "screen, so visitors have to scroll sideways.",
                **details,
            )
        return self.result("pass", "ok", "The page fits the phone screen.", **details)
