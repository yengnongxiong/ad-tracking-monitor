"""Build PageCapture objects in a line or two, for fast analyzer tests without a browser."""

from datetime import UTC, datetime

from tagmonitor.page_capture import (
    Device,
    DomFacts,
    Navigation,
    NetworkRequest,
    PageCapture,
    PerformanceMetrics,
)

SITE_URL = "https://shop.example.com/"


def req(
    url: str,
    *,
    method: str = "GET",
    status: int | None = 200,
    failure: str | None = None,
    post_data: str | None = None,
) -> NetworkRequest:
    return NetworkRequest(
        url=url,
        method=method,
        resource_type="other",
        status=None if failure else status,
        failure=failure,
        started_ms=0,
        post_data=post_data,
    )


def dom(**overrides: object) -> DomFacts:
    values: dict[str, object] = {
        "title": "Shop",
        "viewport_meta": "width=device-width, initial-scale=1",
        "scroll_width": 412,
        "inner_width": 412,
        "viewport_width": 412,
    }
    return DomFacts.model_validate(values | overrides)


def capture(
    *requests: NetworkRequest,
    url: str = SITE_URL,
    device: Device = "mobile",
    status: int | None = 200,
    error_code: str | None = None,
    final_url: str | None = None,
    redirect_chain: list[str] | None = None,
    dom_facts: DomFacts | None = None,
    lcp_ms: float | None = 1200.0,
    page_errors: list[str] | None = None,
    console_errors: list[str] | None = None,
    throttling: str = "none",
) -> PageCapture:
    loaded = error_code is None and status is not None
    return PageCapture(
        url=url,
        device=device,
        user_agent="test",
        throttling=throttling,
        started_at=datetime(2026, 9, 1, tzinfo=UTC),
        duration_ms=3000,
        navigation=Navigation(
            requested_url=url,
            final_url=final_url or url,
            redirect_chain=redirect_chain or [],
            status=status if loaded else None,
            load_event_fired=loaded,
            error_code=error_code,
            error=None if loaded else "net::ERR_SOMETHING",
        ),
        requests=[req(url, status=status or 200), *requests],
        page_errors=page_errors or [],
        console_errors=console_errors or [],
        dom=(dom_facts or dom()) if loaded else None,
        performance=PerformanceMetrics(lcp_ms=lcp_ms, load_ms=1500, request_count=1),
    )


# Real-world URLs the tags produce.
FBEVENTS = "https://connect.facebook.net/en_US/fbevents.js"


def meta_hit(pixel_id: str, event: str = "PageView") -> NetworkRequest:
    return req(f"https://www.facebook.com/tr/?id={pixel_id}&ev={event}", status=200)


def gtag(tag_id: str) -> NetworkRequest:
    return req(f"https://www.googletagmanager.com/gtag/js?id={tag_id}")


def gtm(container_id: str, **kwargs: object) -> NetworkRequest:
    return req(f"https://www.googletagmanager.com/gtm.js?id={container_id}", **kwargs)  # type: ignore[arg-type]


def ga4_hit(measurement_id: str, event: str = "page_view") -> NetworkRequest:
    return req(
        f"https://region1.google-analytics.com/g/collect?v=2&tid={measurement_id}&en={event}",
        method="POST",
        status=204,
    )


def ads_hit(numeric_id: str) -> NetworkRequest:
    return req(
        f"https://googleads.g.doubleclick.net/pagead/viewthroughconversion/{numeric_id}/?random=1"
    )
