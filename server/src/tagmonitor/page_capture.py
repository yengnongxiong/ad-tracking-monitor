"""PageCapture: everything we observed during one page load, as plain serializable data.

This is the contract between the slow, flaky part of the system (the browser, browser/) and
the logic (checks/). Checks only ever see a PageCapture, so they can be unit-tested from saved
JSON and old captures can be re-analyzed without loading the page again (ADR-005).

Bump CAPTURE_SCHEMA_VERSION whenever a field changes meaning or is removed.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

CAPTURE_SCHEMA_VERSION = 1

type Device = Literal["mobile", "desktop"]


class NetworkRequest(BaseModel):
    url: str
    method: str
    resource_type: str  # document, script, image, xhr, fetch, ping, ...
    status: int | None = None  # None when the request failed before a response arrived
    failure: str | None = None  # e.g. "net::ERR_NAME_NOT_RESOLVED"
    started_ms: float  # when the browser issued it, relative to the start of the capture
    duration_ms: float | None = None
    response_bytes: int | None = None  # headers + body as transferred
    post_data: str | None = None  # kept only for tracking endpoints (Meta /tr, GA4 /g/collect)


class BlockedConnection(BaseModel):
    """A connection the egress proxy refused or couldn't make (SSRF guard, DNS, connect)."""

    host: str
    port: int
    code: str
    detail: str


class Navigation(BaseModel):
    requested_url: str
    final_url: str | None = None
    redirect_chain: list[str] = Field(default_factory=list)  # HTTP redirects, in order
    status: int | None = None  # HTTP status of the final main document
    load_event_fired: bool = False
    # Set when no document could be loaded at all:
    # timeout | dns_failure | connection_failed | ssrf_blocked | tls_error | navigation_failed
    error_code: str | None = None
    error: str | None = None

    @property
    def document_loaded(self) -> bool:
        """True when the browser received a main document (even an HTTP error page)."""
        return self.error_code is None and self.status is not None


class DomFacts(BaseModel):
    title: str | None = None
    meta_description: str | None = None
    viewport_meta: str | None = None
    h1: list[str] = Field(default_factory=list)
    h2: list[str] = Field(default_factory=list)
    above_fold_text: str = ""  # visible text in the first viewport, at most 1,500 characters
    button_texts: list[str] = Field(default_factory=list)  # visible buttons and links, at most 30
    scroll_width: int  # document.documentElement.scrollWidth
    inner_width: int  # window.innerWidth; on emulated phones Chrome widens it to fit content
    viewport_width: int  # the device's CSS viewport width (412 on the Pixel 7 profile)


class PerformanceMetrics(BaseModel):
    lcp_ms: float | None = None  # Largest Contentful Paint, lab measurement
    layout_shift: float | None = None  # sum of unexpected layout shifts (simplified CLS)
    load_ms: float | None = None  # navigation start to the end of the load event
    transfer_bytes: int = 0
    request_count: int = 0


class PageCapture(BaseModel):
    capture_schema_version: int = CAPTURE_SCHEMA_VERSION
    url: str
    device: Device
    user_agent: str
    throttling: str  # "none" or a profile name from browser/devices.py
    started_at: datetime
    duration_ms: float
    navigation: Navigation
    requests: list[NetworkRequest] = Field(default_factory=list)
    blocked: list[BlockedConnection] = Field(default_factory=list)
    console_errors: list[str] = Field(default_factory=list)  # at most 10
    page_errors: list[str] = Field(default_factory=list)  # uncaught exceptions, at most 10
    dom: DomFacts | None = None  # None when no document loaded
    performance: PerformanceMetrics = Field(default_factory=PerformanceMetrics)
    # Object storage key of this capture's screenshot, filled in by the worker after upload.
    screenshot_key: str | None = None
