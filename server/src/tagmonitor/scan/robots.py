"""Does a site's robots.txt allow us to load a page? (PRD §16, via urllib.robotparser)

The fetch goes through the same SSRF egress proxy as the browser: robots.txt is a request to
a URL we were given, so it gets the same guard, including on redirects.

Outcomes follow RFC 9309: a 4xx means "no rules, go ahead"; a 5xx means "assume everything is
disallowed" (we skip the site); a network failure means the site is unreachable.
"""

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx

from tagmonitor.browser.egress_proxy import PROXY_ERROR_HEADER, EgressProxy
from tagmonitor.browser.ssrf import Resolver, SsrfPolicy

# The product token robots.txt rules can target to opt out of the research scan.
ROBOTS_USER_AGENT = "tag-monitor"
MAX_ROBOTS_BYTES = 512 * 1024  # RFC 9309 asks crawlers to read at least 500 KiB


@dataclass(frozen=True)
class RobotsVerdict:
    outcome: Literal["allowed", "disallowed", "robots_error", "unreachable"]
    detail: str = ""


def robots_url(page_url: str) -> str:
    parts = urlsplit(page_url)
    return urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))


async def check_robots(page_url: str, policy: SsrfPolicy, resolver: Resolver) -> RobotsVerdict:
    async with (
        EgressProxy(policy, resolver) as proxy,
        httpx.AsyncClient(
            proxy=proxy.url,
            trust_env=False,  # only our guarded proxy, never an environment proxy
            follow_redirects=True,
            timeout=15,
            headers={"User-Agent": f"{ROBOTS_USER_AGENT} (research scan)"},
        ) as client,
    ):
        try:
            response = await client.get(robots_url(page_url))
        except httpx.HTTPError as exc:
            return RobotsVerdict("unreachable", str(exc) or type(exc).__name__)

    refused = response.headers.get(PROXY_ERROR_HEADER)
    if refused:  # our egress proxy answered: DNS failure, refused connection, or SSRF block
        return RobotsVerdict("unreachable", f"egress proxy: {refused}")
    if response.status_code >= 500:
        return RobotsVerdict("robots_error", f"robots.txt returned HTTP {response.status_code}")
    if response.status_code >= 400:
        return RobotsVerdict("allowed", f"no robots.txt (HTTP {response.status_code})")
    parser = RobotFileParser()
    parser.parse(response.text[:MAX_ROBOTS_BYTES].splitlines())
    if parser.can_fetch(ROBOTS_USER_AGENT, page_url):
        return RobotsVerdict("allowed")
    return RobotsVerdict("disallowed", "robots.txt disallows this page")
