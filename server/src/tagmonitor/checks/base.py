"""The Check contract: a pure function from (PageCapture, SiteConfig) to a CheckResult.

Checks never touch the browser, the network or the database (ADR-005). Adding a check means
writing one subclass and adding it to checks/registry.py.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field

from tagmonitor.page_capture import Device, PageCapture

type Status = Literal["pass", "warn", "fail", "info", "error"]

# Worst first. "error" means "could not evaluate", so it ranks below every real result and
# only wins when nothing else ran. Keep in sync with the site_latest_status view (0001_init.sql).
_SEVERITY: dict[Status, int] = {"fail": 5, "warn": 4, "info": 3, "pass": 2, "error": 1}


def worst_status(statuses: Iterable[Status]) -> Status:
    """The status to show (or alert on) when a check ran on several devices."""
    return max(statuses, key=_SEVERITY.__getitem__, default="error")


class SiteConfig(BaseModel):
    """The per-site settings a check may need (a subset of the sites row)."""

    url: str
    expected_meta_pixel_ids: list[str] = Field(default_factory=list)
    expected_ga4_ids: list[str] = Field(default_factory=list)
    expected_google_ads_ids: list[str] = Field(default_factory=list)


class CheckResult(BaseModel):
    check_key: str
    status: Status
    # Machine-readable outcome, e.g. "installed_not_firing". Keys the plain-English
    # explanation in checks/explanations.py, shared by the UI and the emails.
    code: str
    summary: str  # one plain-English sentence
    details: dict[str, Any] = Field(default_factory=dict)


class Check(ABC):
    check_key: ClassVar[str]
    title: ClassVar[str]  # short name for people, e.g. "Meta Pixel"
    devices: ClassVar[frozenset[Device]] = frozenset({"mobile", "desktop"})
    # Every code analyze() can return besides the shared "not_evaluated"/"check_crashed".
    # A test checks that each one has a plain-English explanation.
    codes: ClassVar[frozenset[str]]

    @abstractmethod
    def analyze(self, capture: PageCapture, site: SiteConfig) -> CheckResult: ...

    def result(self, status: Status, code: str, summary: str, **details: Any) -> CheckResult:
        return CheckResult(
            check_key=self.check_key, status=status, code=code, summary=summary, details=details
        )

    def not_evaluated(self, capture: PageCapture) -> CheckResult:
        """For checks that need a loaded page. The state machine ignores "error" results, so
        an outage produces one Page health alert instead of one alert per check."""
        reason = capture.navigation.error_code or "no_document"
        return self.result(
            "error",
            "not_evaluated",
            "Not checked: the page didn't load.",
            navigation_error=reason,
        )
