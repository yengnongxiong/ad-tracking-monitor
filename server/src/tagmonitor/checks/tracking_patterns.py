"""Which network requests are tracking traffic.

The capture step uses this to decide which POST bodies to keep (the rest are dropped, since
they can be large and may hold form data). The Meta and Google checks (M3) build on it.
"""

from urllib.parse import urlsplit


def _host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith(f".{domain}")


def is_meta_hit(url: str) -> bool:
    """A Meta Pixel event: facebook.com/tr (GET image beacon or POST)."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return _host_matches(host, "facebook.com") and parts.path.rstrip("/") == "/tr"


def is_ga4_hit(url: str) -> bool:
    """A GA4 measurement protocol request: */g/collect on google-analytics.com."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    on_ga_host = _host_matches(host, "google-analytics.com") or host == "analytics.google.com"
    return on_ga_host and parts.path.endswith("/g/collect")


def is_tracking_hit(url: str) -> bool:
    """Requests whose body carries tracking events worth keeping in the capture."""
    return is_meta_hit(url) or is_ga4_hit(url)
