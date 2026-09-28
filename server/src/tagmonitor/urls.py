"""URL helpers shared by the checks, the API and the scheduler."""

from urllib.parse import urlsplit

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
