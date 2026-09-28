"""Recognize Meta and Google tag traffic in captured network requests.

These endpoints are not documented APIs; they are what the tags' own JavaScript sends today,
and vendors change them occasionally. Keep every pattern here, keep it unit-tested, and
re-verify against live sites from time to time:

    python -m tagmonitor.verify_patterns https://site-one.example https://site-two.example

The command prints which requests matched and, more importantly, requests to Meta/Google
tag hosts that matched nothing (a sign that an endpoint moved).

Last verified against live sites: NOT YET. The build environment has no general internet
access; the first verification run and its date belong here and in docs/milestones/M3.md.
"""

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from tagmonitor.page_capture import NetworkRequest

# Hosts that only serve tag traffic. Used to spot requests we failed to classify.
TAG_HOSTS = (
    "connect.facebook.net",
    "facebook.com",
    "googletagmanager.com",
    "google-analytics.com",
    "analytics.google.com",
    "doubleclick.net",
    "googleadservices.com",
)

_META_CONFIG_PATH = re.compile(r"^/signals/config/(\d+)")
_ADS_PATH = re.compile(r"^/pagead/(?:viewthroughconversion|conversion)/(\d+)/?", re.IGNORECASE)
_MULTIPART_FIELD = re.compile(
    rb'Content-Disposition:\s*form-data;\s*name="([^"]+)"\r?\n\r?\n([^\r\n]*)', re.IGNORECASE
)


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith(f".{domain}")


def is_tag_host(url: str) -> bool:
    host = _host(url)
    return any(_host_matches(host, domain) for domain in TAG_HOSTS)


def _first(params: dict[str, list[str]], key: str) -> str | None:
    values = params.get(key)
    return values[0] if values else None


def _form_fields(body: str) -> dict[str, list[str]]:
    """Fields of a urlencoded or multipart/form-data body (the two formats tags POST)."""
    if "form-data" in body[:500].lower():
        fields: dict[str, list[str]] = {}
        for name, value in _MULTIPART_FIELD.findall(body.encode()):
            fields.setdefault(name.decode(), []).append(value.decode())
        return fields
    return parse_qs(body)


# -- Meta Pixel ----------------------------------------------------------------------------


def is_meta_script(url: str) -> bool:
    """The pixel base code: connect.facebook.net/<locale>/fbevents.js."""
    parts = urlsplit(url)
    return _host_matches(_host(url), "connect.facebook.net") and parts.path.endswith("/fbevents.js")


def meta_config_pixel_id(url: str) -> str | None:
    """fbevents.js fetches connect.facebook.net/signals/config/<pixel id> for each pixel."""
    if not _host_matches(_host(url), "connect.facebook.net"):
        return None
    match = _META_CONFIG_PATH.match(urlsplit(url).path)
    return match.group(1) if match else None


def is_meta_hit(url: str) -> bool:
    """A Meta Pixel event: facebook.com/tr (GET image beacon or POST)."""
    parts = urlsplit(url)
    return _host_matches(_host(url), "facebook.com") and parts.path.rstrip("/") == "/tr"


@dataclass(frozen=True)
class MetaHit:
    pixel_id: str
    event: str


def parse_meta_hit(request: NetworkRequest) -> MetaHit | None:
    """Pixel id and event name, from the query string or (for POSTs) the form body."""
    if not is_meta_hit(request.url):
        return None
    params = parse_qs(urlsplit(request.url).query)
    if request.post_data:
        params = _form_fields(request.post_data) | params
    pixel_id, event = _first(params, "id"), _first(params, "ev")
    if not pixel_id or not event:
        return None
    return MetaHit(pixel_id=pixel_id, event=event)


# -- Google tags -----------------------------------------------------------------------------


@dataclass(frozen=True)
class GoogleScript:
    kind: str  # "gtm" (gtm.js) or "gtag" (gtag.js)
    tag_id: str  # GTM-..., G-..., AW-... or GT-...


def parse_google_script(url: str) -> GoogleScript | None:
    """googletagmanager.com/gtm.js?id=GTM-... or googletagmanager.com/gtag/js?id=G-|AW-|GT-..."""
    if not _host_matches(_host(url), "googletagmanager.com"):
        return None
    parts = urlsplit(url)
    tag_id = _first(parse_qs(parts.query), "id")
    if not tag_id:
        return None
    tag_id = tag_id.upper()
    if parts.path == "/gtm.js" and tag_id.startswith("GTM-"):
        return GoogleScript("gtm", tag_id)
    if parts.path == "/gtag/js" and tag_id.startswith(("G-", "AW-", "GT-")):
        return GoogleScript("gtag", tag_id)
    return None


def is_ga4_hit(url: str) -> bool:
    """A GA4 measurement request: /g/collect on *.google-analytics.com or analytics.google.com."""
    host = _host(url)
    on_ga_host = _host_matches(host, "google-analytics.com") or host == "analytics.google.com"
    return on_ga_host and urlsplit(url).path.endswith("/g/collect")


@dataclass(frozen=True)
class Ga4Hit:
    measurement_id: str
    event: str


def parse_ga4_hits(request: NetworkRequest) -> list[Ga4Hit]:
    """Events in one GA4 request.

    A single event carries `en` in the query string. gtag.js also batches: shared parameters
    (like `tid`) stay in the query string and each event is one line of the POST body.
    """
    if not is_ga4_hit(request.url):
        return []
    query = parse_qs(urlsplit(request.url).query)
    lines = [line for line in (request.post_data or "").splitlines() if line.strip()]
    events = [parse_qs(line) for line in lines] or [query]
    hits = []
    for event_params in events:
        measurement_id = _first(event_params, "tid") or _first(query, "tid")
        event = _first(event_params, "en") or _first(query, "en")
        if measurement_id and event:
            hits.append(Ga4Hit(measurement_id=measurement_id.upper(), event=event))
    return hits


@dataclass(frozen=True)
class AdsHit:
    conversion_id: str  # "AW-123456789"
    kind: str  # "remarketing" (page view) or "conversion"


def parse_ads_hit(url: str) -> AdsHit | None:
    """Google Ads: remarketing page views and conversions.

    googleads.g.doubleclick.net/pagead/viewthroughconversion/<id>/ is sent on page view by an
    AW- tag; googleadservices.com/pagead/conversion/<id>/ is a conversion.
    """
    host = _host(url)
    if not (
        _host_matches(host, "googleads.g.doubleclick.net")
        or _host_matches(host, "googleadservices.com")
    ):
        return None
    match = _ADS_PATH.match(urlsplit(url).path)
    if not match:
        return None
    kind = "remarketing" if "viewthroughconversion" in url.lower() else "conversion"
    return AdsHit(conversion_id=f"AW-{match.group(1)}", kind=kind)


def normalize_ads_id(value: str) -> str:
    """Users paste "AW-123", "aw-123" or just "123"; compare them all as "AW-123"."""
    value = value.strip().upper()
    return value if value.startswith("AW-") else f"AW-{value}"


def classify(request: NetworkRequest) -> str | None:
    """Label a request with the pattern it matches, or None. Used by verify_patterns."""
    url = request.url
    if is_meta_script(url):
        return "meta script"
    if meta_config_pixel_id(url):
        return "meta config"
    if parse_meta_hit(request):
        return "meta hit"
    if parse_google_script(url):
        return "google script"
    if parse_ga4_hits(request):
        return "ga4 hit"
    if parse_ads_hit(url):
        return "ads hit"
    return None


def is_tracking_hit(url: str) -> bool:
    """Requests whose body carries tracking events worth keeping in the capture."""
    return is_meta_hit(url) or is_ga4_hit(url)
