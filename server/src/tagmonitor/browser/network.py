"""Records every request a page makes, as observed by the browser."""

import asyncio
from collections.abc import Coroutine
from dataclasses import dataclass, field
from typing import Any

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page, Request, Response

from tagmonitor.browser.egress_proxy import PROXY_ERROR_HEADER
from tagmonitor.checks.tracking_patterns import is_tracking_hit
from tagmonitor.page_capture import NetworkRequest

MAX_POST_BODY_CHARS = 64 * 1024


@dataclass
class _InFlight:
    record: NetworkRequest
    started_at: float  # event-loop clock, for durations


@dataclass
class NetworkRecorder:
    """Attach to a page before navigating; call finish() once the page is done."""

    page: Page
    capture_started_at: float  # event-loop clock when the capture began
    last_activity_at: float = 0.0  # when a request last started or finished
    main_document: Response | None = None  # final main-frame document response
    last_navigation_url: str | None = None  # last main-frame document request (even if failed)
    _requests: dict[Request, _InFlight] = field(default_factory=dict)
    _pending: set[asyncio.Task[None]] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.last_activity_at = self._now()
        self.page.on("request", self._on_request)
        self.page.on("response", self._on_response)
        self.page.on("requestfinished", self._on_finished)
        self.page.on("requestfailed", self._on_failed)

    def _now(self) -> float:
        return asyncio.get_running_loop().time()

    def _on_request(self, request: Request) -> None:
        now = self._now()
        self.last_activity_at = now
        if self._is_main_document(request):
            self.last_navigation_url = request.url
        post_data = None
        if request.method == "POST" and is_tracking_hit(request.url):
            buffer = request.post_data_buffer
            if buffer:
                post_data = buffer.decode("utf-8", errors="replace")[:MAX_POST_BODY_CHARS]
        record = NetworkRequest(
            url=request.url,
            method=request.method,
            resource_type=request.resource_type,
            started_ms=(now - self.capture_started_at) * 1000,
            post_data=post_data,
        )
        self._requests[request] = _InFlight(record, now)

    def _on_response(self, response: Response) -> None:
        if self._is_main_document(response.request):
            self.main_document = response

    def _on_finished(self, request: Request) -> None:
        self.last_activity_at = self._now()
        self._track(self._complete(request))

    def _on_failed(self, request: Request) -> None:
        self.last_activity_at = self._now()
        entry = self._requests.get(request)
        if entry is not None:
            entry.record.failure = request.failure
            entry.record.duration_ms = (self._now() - entry.started_at) * 1000

    def _is_main_document(self, request: Request) -> bool:
        try:
            return request.is_navigation_request() and request.frame == self.page.main_frame
        except PlaywrightError:
            return False  # the frame is already gone

    def _track(self, coroutine: Coroutine[Any, Any, None]) -> None:
        """Playwright event callbacks are synchronous, so async follow-ups run as tasks."""
        task = asyncio.create_task(coroutine)
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _complete(self, request: Request) -> None:
        entry = self._requests.get(request)
        if entry is None:
            return
        entry.record.duration_ms = (self._now() - entry.started_at) * 1000
        try:
            response = await request.response()
            sizes = await request.sizes()
        except PlaywrightError:
            return  # page or context closed while we were asking
        if response is not None:
            entry.record.status = response.status
            proxy_error = response.headers.get(PROXY_ERROR_HEADER.lower())
            if proxy_error:
                entry.record.failure = f"refused by egress proxy: {proxy_error}"
        entry.record.response_bytes = sizes["responseHeadersSize"] + sizes["responseBodySize"]

    async def wait_for_quiet(self, quiet_s: float = 2.0, max_s: float = 10.0) -> None:
        """Wait until no request has started or finished for `quiet_s`, but at most `max_s`.

        Tags often fire after the load event (async scripts, timers), so we keep listening
        briefly. Counting finishes as activity also gives the browser time to paint (and
        report LCP for) a resource that just arrived. A request that simply stays open, like
        a long-poll, produces no activity, so it can't hold us until the maximum.
        """
        deadline = self._now() + max_s
        while True:
            now = self._now()
            if now - self.last_activity_at >= quiet_s or now >= deadline:
                return
            await asyncio.sleep(0.1)

    async def finish(self) -> list[NetworkRequest]:
        """Wait (briefly) for response details still being fetched, then return all requests."""
        if self._pending:
            await asyncio.wait(self._pending, timeout=5)
        return [entry.record for entry in self._requests.values()]
