"""URL helpers shared by the checks, the API and the scheduler."""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import tldextract

from tagmonitor.browser.ssrf import normalize_host

# Offline: the bundled public-suffix snapshot, never a network fetch at runtime. Private
# suffixes are included so shop-a.myshopify.com and shop-b.myshopify.com count as different
# sites, which is what "the landing page moved to another site" should mean.
_extract = tldextract.TLDExtract(
    suffix_list_urls=(), cache_dir=None, include_psl_private_domains=True
)


def registrable_domain(url: str) -> str:
    """Return example.co.uk for https://shop.example.co.uk/, or the bare host for IPs etc."""
    host = normalize_host(urlsplit(url).hostname or "")
    return _extract(host).top_domain_under_public_suffix or host


# Added by ad platforms and campaign links; they don't change which page it is.
_TRACKING_PARAMS = ("fbclid", "gclid", "gbraid", "wbraid", "msclkid", "ttclid", "dclid")


def _is_tracking_param(name: str) -> bool:
    name = name.lower()
    return name.startswith("utm_") or name in _TRACKING_PARAMS


def normalize_url(url: str) -> str:
    """The canonical form used to spot duplicate sites.

    Lowercase scheme and host, no default port, no fragment, "/" for an empty path, ad-click
    and UTM parameters dropped, and the remaining query parameters sorted. The URL we load
    stays exactly what the user typed; this is only for "is it the same page?".
    """
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = normalize_host(parts.hostname or "")
    if ":" in host:
        host = f"[{host}]"  # IPv6 literal
    default_port = {"http": 80, "https": 443}.get(scheme)
    netloc = host if parts.port in (None, default_port) else f"{host}:{parts.port}"
    params = sorted(
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not _is_tracking_param(key)
    )
    return urlunsplit((scheme, netloc, parts.path or "/", urlencode(params), ""))
