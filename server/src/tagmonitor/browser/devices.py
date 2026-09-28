"""Device profiles and optional throttling for page captures."""

from dataclasses import dataclass
from typing import Any

from playwright.async_api import CDPSession, Playwright

from tagmonitor.page_capture import Device

DESKTOP_VIEWPORT = {"width": 1440, "height": 900}


def context_options(playwright: Playwright, device: Device) -> dict[str, Any]:
    """Browser context options for a device.

    Mobile is Playwright's "Pixel 7" descriptor (viewport, pixel ratio, touch, mobile UA).
    Desktop is a 1440x900 viewport with the matching desktop Chrome user agent. Both UAs look
    like regular Chrome, not HeadlessChrome, because some sites serve bots different pages.
    """
    if device == "mobile":
        descriptor = dict(playwright.devices["Pixel 7"])
    else:
        descriptor = dict(playwright.devices["Desktop Chrome"]) | {"viewport": DESKTOP_VIEWPORT}
    descriptor.pop("default_browser_type", None)  # describes the device, not a context option
    return descriptor


@dataclass(frozen=True)
class ThrottleProfile:
    request_latency_ms: float
    download_kbps: float
    upload_kbps: float
    cpu_slowdown: float


# Lighthouse's "applied" (DevTools) mobile throttling: a 150 ms RTT, 1.6 Mbps connection
# expressed as per-request latency 562.5 ms, 1474.56 Kbps down, 675 Kbps up, plus a 4x CPU
# slowdown. Source: Lighthouse's throttling constants (mobileSlow4G, DEVTOOLS_* multipliers).
THROTTLING_PROFILES: dict[str, ThrottleProfile] = {
    "slow4g": ThrottleProfile(
        request_latency_ms=562.5, download_kbps=1474.56, upload_kbps=675, cpu_slowdown=4
    ),
}


async def apply_throttling(cdp: CDPSession, profile: ThrottleProfile) -> None:
    """Emulate a slow phone through the Chrome DevTools Protocol for this page."""
    await cdp.send("Network.enable")
    await cdp.send(
        "Network.emulateNetworkConditions",
        {
            "offline": False,
            "latency": profile.request_latency_ms,
            "downloadThroughput": profile.download_kbps * 1024 / 8,  # CDP wants bytes/second
            "uploadThroughput": profile.upload_kbps * 1024 / 8,
        },
    )
    await cdp.send("Emulation.setCPUThrottlingRate", {"rate": profile.cpu_slowdown})
