"""PageCapturer: load a URL in headless Chromium and record what happened (PRD §9).

One Chromium per worker process (launching one costs about a second), relaunched after 200
captures or after a crash to cap memory growth. Each capture gets a fresh browser context,
so no cookies or cache leak between sites, and its own egress proxy, so every connection
passes the SSRF guard.
"""

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from types import TracebackType
from typing import Self
from urllib.parse import urlsplit

from playwright.async_api import Browser, ConsoleMessage, Page, Playwright, async_playwright
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from tagmonitor.browser import page_scripts
from tagmonitor.browser.devices import THROTTLING_PROFILES, apply_throttling, context_options
from tagmonitor.browser.egress_proxy import PROXY_ERROR_HEADER, EgressProxy
from tagmonitor.browser.network import NetworkRecorder
from tagmonitor.browser.ssrf import (
    Resolver,
    SsrfPolicy,
    SystemResolver,
    normalize_host,
    validate_user_url,
)
from tagmonitor.browser.tracking_stubs import install_tracking_stubs
from tagmonitor.page_capture import (
    BlockedConnection,
    Device,
    DomFacts,
    Navigation,
    PageCapture,
    PerformanceMetrics,
)

log = logging.getLogger(__name__)

NAVIGATION_TIMEOUT_MS = 30_000
HARD_TIMEOUT_S = 60.0
RELAUNCH_AFTER_CAPTURES = 200
MAX_MESSAGES = 10

# WebRTC can open UDP connections that bypass HTTP proxies entirely; this makes Chromium send
# WebRTC through the proxy or not at all.
CHROMIUM_ARGS = ["--force-webrtc-ip-handling-policy=disable_non_proxied_udp"]


class CaptureError(Exception):
    """The capture itself failed (not the website). `transient` failures are worth a retry."""

    def __init__(self, code: str, message: str, *, transient: bool) -> None:
        super().__init__(message)
        self.code = code
        self.transient = transient


@dataclass
class CaptureResult:
    capture: PageCapture
    screenshot_jpeg: bytes | None  # viewport only; None when no document loaded


class PageCapturer:
    def __init__(
        self,
        *,
        policy: SsrfPolicy,
        resolver: Resolver | None = None,
        tracking_stubs: bool = False,
        throttling: str = "none",
        relaunch_after: int = RELAUNCH_AFTER_CAPTURES,
    ) -> None:
        if throttling != "none" and throttling not in THROTTLING_PROFILES:
            raise ValueError(f"unknown throttling profile {throttling!r}")
        self.policy = policy
        self.resolver = resolver or SystemResolver()
        self.tracking_stubs = tracking_stubs
        self.throttling = throttling
        self.relaunch_after = relaunch_after
        self.browser_launches = 0
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._captures_on_browser = 0

    async def __aenter__(self) -> Self:
        self._playwright = await async_playwright().start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._close_browser()
        if self._playwright is not None:
            await self._playwright.stop()

    async def _close_browser(self) -> None:
        if self._browser is not None:
            with contextlib.suppress(PlaywrightError):
                await self._browser.close()
            self._browser = None

    async def _get_browser(self) -> Browser:
        assert self._playwright is not None, "use `async with PageCapturer(...)`"
        needs_launch = (
            self._browser is None
            or not self._browser.is_connected()
            or self._captures_on_browser >= self.relaunch_after
        )
        if needs_launch:
            await self._close_browser()
            self._browser = await self._playwright.chromium.launch(args=CHROMIUM_ARGS)
            self._captures_on_browser = 0
            self.browser_launches += 1
        assert self._browser is not None
        self._captures_on_browser += 1
        return self._browser

    async def capture(self, url: str, device: Device) -> CaptureResult:
        """Capture one URL on one device.

        Website problems (timeouts, DNS failures, HTTP errors, a blocked redirect) are data:
        they come back inside the PageCapture. Only failures of the capture machinery raise
        CaptureError. An invalid user URL raises SsrfError before the browser is involved.
        """
        validate_user_url(url, self.policy)
        browser = await self._get_browser()
        try:
            async with asyncio.timeout(HARD_TIMEOUT_S):
                return await self._capture(browser, url, device)
        except TimeoutError as exc:
            raise CaptureError(
                "capture_timeout", f"capture exceeded {HARD_TIMEOUT_S:.0f} s", transient=True
            ) from exc
        except PlaywrightError as exc:
            if not browser.is_connected():
                self._browser = None
                raise CaptureError("browser_crash", str(exc), transient=True) from exc
            raise CaptureError("capture_failed", str(exc), transient=True) from exc

    async def _capture(self, browser: Browser, url: str, device: Device) -> CaptureResult:
        assert self._playwright is not None
        loop = asyncio.get_running_loop()
        started_at = datetime.now(UTC)
        clock_start = loop.time()
        throttling = self.throttling if device == "mobile" else "none"

        async with EgressProxy(self.policy, self.resolver) as proxy:
            options = context_options(self._playwright, device)
            context = await browser.new_context(
                **options,
                proxy={"server": proxy.url},
                service_workers="block",  # SW fetches could escape context-level observation
                accept_downloads=False,
            )
            try:
                if self.tracking_stubs:
                    await install_tracking_stubs(context)
                await context.add_init_script(script=page_scripts.PERFORMANCE_OBSERVER)
                page = await context.new_page()
                crashed = asyncio.Event()
                page.on("crash", lambda _page: crashed.set())
                console_errors: list[str] = []
                page_errors: list[str] = []

                def on_console(message: ConsoleMessage) -> None:
                    if message.type == "error":
                        _append_unique(console_errors, message.text)

                page.on("console", on_console)
                page.on("pageerror", lambda error: _append_unique(page_errors, str(error)))
                recorder = NetworkRecorder(page, clock_start)
                if throttling != "none":
                    cdp = await context.new_cdp_session(page)
                    await apply_throttling(cdp, THROTTLING_PROFILES[throttling])

                navigation = await _navigate(page, url, recorder, proxy)
                dom: DomFacts | None = None
                performance = PerformanceMetrics()
                screenshot: bytes | None = None
                if navigation.document_loaded:
                    await recorder.wait_for_quiet()
                    dom = DomFacts.model_validate(
                        await page.evaluate(page_scripts.DOM_FACTS)
                        | {"viewport_width": options["viewport"]["width"]}
                    )
                    performance = PerformanceMetrics.model_validate(
                        await page.evaluate(page_scripts.READ_PERFORMANCE)
                    )
                    screenshot = await page.screenshot(type="jpeg", quality=70)
                    navigation.final_url = page.url
                if crashed.is_set():
                    raise CaptureError("page_crash", "the page's renderer crashed", transient=True)

                requests = await recorder.finish()
                performance.request_count = len(requests)
                performance.transfer_bytes = sum(r.response_bytes or 0 for r in requests)
                capture = PageCapture(
                    url=url,
                    device=device,
                    user_agent=str(options["user_agent"]),
                    throttling=throttling,
                    started_at=started_at,
                    duration_ms=(loop.time() - clock_start) * 1000,
                    navigation=navigation,
                    requests=requests,
                    blocked=[
                        BlockedConnection(host=f.host, port=f.port, code=f.code, detail=f.detail)
                        for f in proxy.failures
                    ],
                    console_errors=console_errors,
                    page_errors=page_errors,
                    dom=dom,
                    performance=performance,
                )
                return CaptureResult(capture=capture, screenshot_jpeg=screenshot)
            finally:
                # A wedged browser must not hang the worker; the next capture relaunches it.
                try:
                    await asyncio.wait_for(context.close(), 10)
                except (TimeoutError, PlaywrightError):
                    log.warning("could not close browser context; relaunching browser")
                    await self._close_browser()


def _append_unique(messages: list[str], message: str) -> None:
    message = message[:500]
    if len(messages) < MAX_MESSAGES and message not in messages:
        messages.append(message)


async def _navigate(
    page: Page, url: str, recorder: NetworkRecorder, proxy: EgressProxy
) -> Navigation:
    """Load the page and describe the outcome. Never raises for website problems."""
    load_event_fired = False
    error_message: str | None = None
    try:
        await page.goto(url, wait_until="load", timeout=NAVIGATION_TIMEOUT_MS)
        load_event_fired = True
    except PlaywrightTimeoutError as exc:
        error_message = str(exc).splitlines()[0]
    except PlaywrightError as exc:
        error_message = str(exc).splitlines()[0]

    navigation = Navigation(requested_url=url, load_event_fired=load_event_fired)
    document = recorder.main_document
    if document is not None:
        chain: list[str] = []
        hop = document.request.redirected_from
        while hop is not None:
            chain.append(hop.url)
            hop = hop.redirected_from
        navigation.redirect_chain = list(reversed(chain))
        proxy_error = document.headers.get(PROXY_ERROR_HEADER.lower())
        if proxy_error is None:
            # A document arrived. If "load" never fired (a hanging image or script), the page
            # is still usable, so we keep going and just record that load didn't finish.
            navigation.status = document.status
            return navigation
        navigation.error_code = proxy_error  # our proxy answered on the website's behalf
        navigation.error = f"refused by egress proxy: {proxy_error}"
        navigation.final_url = document.url
        return navigation

    failed_url = recorder.last_navigation_url or url
    navigation.final_url = failed_url
    navigation.error = error_message or "navigation failed"
    navigation.error_code = _classify_navigation_error(navigation.error, failed_url, proxy)
    return navigation


def _classify_navigation_error(message: str, failed_url: str, proxy: EgressProxy) -> str:
    """Turn a browser error into one of a few stable codes the checks and UI understand."""
    host = normalize_host(urlsplit(failed_url).hostname or "")
    for failure in proxy.failures:
        if failure.host == host:
            return failure.code  # ssrf_blocked, dns_failure or connection_failed
    if "Timeout" in message:
        return "timeout"
    if "ERR_NAME_NOT_RESOLVED" in message:
        return "dns_failure"
    if any(code in message for code in ("ERR_CERT", "ERR_SSL")):
        return "tls_error"
    if any(code in message for code in ("ERR_CONNECTION", "ERR_TUNNEL", "ERR_EMPTY_RESPONSE")):
        return "connection_failed"
    return "navigation_failed"
