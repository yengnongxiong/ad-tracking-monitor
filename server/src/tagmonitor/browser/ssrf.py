"""SSRF policy: which URLs we may load and which IP addresses we may connect to.

Users give us arbitrary URLs and our headless browser loads them from inside our network, so a
malicious (or misconfigured) page could make us hit internal services: the cloud metadata
endpoint (169.254.169.254), the database, the admin API. This module holds the pure rules.
browser/egress_proxy.py enforces them on every connection the browser makes (see ADR-004).

The rules:
- User-submitted URLs: http/https only, no credentials, ports 80/443 only.
- Any host we connect to must resolve only to public (globally routable) addresses. "Any"
  matters: a hostname with one public and one private A record is blocked, because we don't
  control which record a client picks.
- IP literals are parsed the way browsers parse them (decimal, hex, octal and shorthand
  forms like http://2130706433/ or http://0x7f.1/ all mean 127.0.0.1).
- IPv6 addresses that embed an IPv4 address (IPv4-mapped, NAT64, 6to4, Teredo) are judged by
  the embedded address too.
- `allow_hosts` exempts exact hostnames from the IP and port rules. It exists only so the
  dev stack and tests can load fixture sites on a private network; it is empty in production.
"""

import asyncio
import ipaddress
import socket
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import SplitResult, urlsplit

type IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

USER_URL_PORTS = frozenset({80, 443})

_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


class SsrfError(Exception):
    """A URL or host the guard refuses. `code` is stable and stored with failed runs."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SsrfPolicy:
    allow_hosts: frozenset[str] = field(default_factory=frozenset)

    def is_allowlisted(self, host: str) -> bool:
        return normalize_host(host) in self.allow_hosts


class Resolver(Protocol):
    async def resolve(self, host: str) -> list[IPAddress]:
        """Return every address the host resolves to; raise SsrfError('dns_failure') if none."""
        ...


class SystemResolver:
    """Resolves through the operating system, like the browser itself would."""

    async def resolve(self, host: str) -> list[IPAddress]:
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except (socket.gaierror, UnicodeError) as exc:
            raise SsrfError("dns_failure", f"could not resolve {host}: {exc}") from exc
        addresses: list[IPAddress] = []
        for _family, _type, _proto, _canonname, sockaddr in infos:
            address = ipaddress.ip_address(str(sockaddr[0]).split("%")[0])  # drop IPv6 zone id
            if address not in addresses:
                addresses.append(address)
        if not addresses:
            raise SsrfError("dns_failure", f"{host} has no addresses")
        return addresses


def normalize_host(host: str) -> str:
    """Lowercase, strip IPv6 brackets and a trailing dot, so comparisons are exact."""
    return host.strip().lower().removeprefix("[").removesuffix("]").rstrip(".")


_HEX_DIGITS = frozenset("0123456789abcdef")


def _ends_in_number(last_label: str) -> bool:
    """WHATWG "ends in a number": decides whether a host is parsed as IPv4 at all."""
    if last_label.isascii() and last_label.isdigit():
        return True
    return last_label.startswith("0x") and set(last_label[2:]) <= _HEX_DIGITS


def _parse_ipv4_part(part: str) -> int | None:
    """One dotted part in the WHATWG URL syntax: decimal, 0x-hex, or 0-prefixed octal."""
    if part == "":
        return None
    if part.startswith("0x"):
        digits, base = part[2:], 16
        if digits == "":
            return 0  # "0x" alone means 0 in the URL standard
    elif len(part) > 1 and part.startswith("0"):
        digits, base = part[1:], 8
    else:
        digits, base = part, 10
    if not digits.isascii():
        return None
    try:
        return int(digits, base)
    except ValueError:
        return None


def parse_ip_literal(host: str) -> IPAddress | None:
    """Parse a host as an IP address the way a browser would, or return None for a hostname.

    Browsers accept legacy IPv4 forms (http://2130706433/, http://0x7f.0.0.1/, http://127.1/),
    so an allowlist or blocklist that only understands dotted-decimal can be bypassed. This
    follows the WHATWG URL standard's IPv4 parser: if the last label looks numeric the host is
    an IPv4 address, and an invalid one is rejected rather than treated as a hostname.
    """
    host = normalize_host(host)
    if ":" in host:
        try:
            return ipaddress.IPv6Address(host)
        except ValueError as exc:
            raise SsrfError("invalid_url", f"invalid IPv6 address {host!r}") from exc

    parts = host.split(".")
    if not _ends_in_number(parts[-1]):
        return None
    if len(parts) > 4:
        raise SsrfError("invalid_url", f"invalid IPv4 address {host!r}")
    numbers = [_parse_ipv4_part(p) for p in parts]
    if any(n is None for n in numbers):
        raise SsrfError("invalid_url", f"invalid IPv4 address {host!r}")
    values = [n for n in numbers if n is not None]
    # All parts but the last are single bytes; the last part fills the remaining bytes.
    if any(n > 255 for n in values[:-1]) or values[-1] >= 256 ** (5 - len(values)):
        raise SsrfError("invalid_url", f"invalid IPv4 address {host!r}")
    address = values[-1]
    for index, byte in enumerate(values[:-1]):
        address += byte * 256 ** (3 - index)
    return ipaddress.IPv4Address(address)


def _embedded_ipv4(address: ipaddress.IPv6Address) -> list[ipaddress.IPv4Address]:
    """IPv4 addresses tunneled inside an IPv6 address (6to4, Teredo, NAT64).

    Gateways translate these to the embedded IPv4 address, so a private one is reachable.
    """
    embedded = [address.sixtofour] if address.sixtofour is not None else []
    if address.teredo is not None:
        embedded.extend(address.teredo)
    if address in _NAT64:
        embedded.append(ipaddress.IPv4Address(int(address) & 0xFFFFFFFF))
    return embedded


def is_blocked_ip(address: IPAddress) -> bool:
    """True for anything that isn't a plain public unicast address."""
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            # ::ffff:a.b.c.d *is* a.b.c.d to the OS, so judge it exactly like the IPv4 address
            # (Python versions disagree on is_private for mapped addresses).
            return is_blocked_ip(address.ipv4_mapped)
        if any(is_blocked_ip(inner) for inner in _embedded_ipv4(address)):
            return True
    return (
        address.is_private  # RFC 1918, IPv6 ULA (fc00::/7), and other special ranges
        or address.is_loopback
        or address.is_link_local  # includes 169.254.169.254, the cloud metadata endpoint
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
        or (isinstance(address, ipaddress.IPv4Address) and address in _CGNAT)
        or not address.is_global  # catch-all for anything else not publicly routable
    )


def validate_user_url(url: str, policy: SsrfPolicy) -> SplitResult:
    """Check the shape of a URL a user submitted (no DNS). Raises SsrfError('invalid_url')."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError as exc:
        raise SsrfError("invalid_url", f"not a valid URL: {exc}") from exc
    if parts.scheme not in ("http", "https"):
        raise SsrfError("invalid_url", "only http:// and https:// URLs can be monitored")
    if not parts.hostname:
        raise SsrfError("invalid_url", "the URL has no host")
    if parts.username is not None or parts.password is not None:
        raise SsrfError("invalid_url", "URLs with a username or password are not allowed")
    if policy.is_allowlisted(parts.hostname):
        return parts
    effective_port = port or (443 if parts.scheme == "https" else 80)
    if effective_port not in USER_URL_PORTS:
        raise SsrfError("invalid_url", "only the standard ports 80 and 443 are allowed")
    parse_ip_literal(parts.hostname)  # rejects malformed numeric hosts early
    return parts


async def resolve_public(host: str, policy: SsrfPolicy, resolver: Resolver) -> list[IPAddress]:
    """Resolve a host and return its addresses, or raise if any of them is not public.

    Allowlisted hosts skip the address check (dev fixtures only). IP literals are checked
    without DNS.
    """
    if policy.is_allowlisted(host):
        return await resolver.resolve(normalize_host(host))
    literal = parse_ip_literal(host)
    addresses = [literal] if literal is not None else await resolver.resolve(normalize_host(host))
    blocked = [a for a in addresses if is_blocked_ip(a)]
    if blocked:
        raise SsrfError(
            "ssrf_blocked", f"{normalize_host(host)} points to a non-public address ({blocked[0]})"
        )
    return addresses


async def check_user_url(url: str, policy: SsrfPolicy, resolver: Resolver) -> SplitResult:
    """Full check for a user-submitted URL: shape rules plus public-address resolution."""
    parts = validate_user_url(url, policy)
    if parts.hostname is None:  # validate_user_url already rejects this; keeps typing honest
        raise SsrfError("invalid_url", "the URL has no host")
    await resolve_public(parts.hostname, policy, resolver)
    return parts
