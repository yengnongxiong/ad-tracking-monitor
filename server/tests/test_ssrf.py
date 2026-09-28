"""The SSRF policy rules (pure functions, no browser)."""

import ipaddress

import pytest

from tagmonitor.browser.ssrf import (
    SsrfError,
    SsrfPolicy,
    check_user_url,
    is_blocked_ip,
    parse_ip_literal,
    resolve_public,
    validate_user_url,
)
from tests.fixture_server import StaticResolver

NO_ALLOWLIST = SsrfPolicy()


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", "127.0.0.1"),
        ("2130706433", "127.0.0.1"),  # one decimal number
        ("0x7f000001", "127.0.0.1"),  # one hex number
        ("0x7F.0.0.1", "127.0.0.1"),  # hex part (hosts arrive lowercased from URLs, too)
        ("0177.0.0.1", "127.0.0.1"),  # octal part
        ("127.1", "127.0.0.1"),  # shorthand: last part fills the remaining bytes
        ("0x7f.1", "127.0.0.1"),
        ("169.254.43518", "169.254.169.254"),
        ("0", "0.0.0.0"),
        ("0x", "0.0.0.0"),
        ("[::1]", "::1"),
        ("::ffff:127.0.0.1", "::ffff:7f00:1"),
        ("8.8.8.8.", "8.8.8.8"),  # trailing dot
    ],
)
def test_parses_ip_literals_like_a_browser(host: str, expected: str) -> None:
    assert parse_ip_literal(host) == ipaddress.ip_address(expected)


@pytest.mark.parametrize("host", ["example.com", "shop.example.co.uk", "1.2.3.example", "0xzz"])
def test_hostnames_are_not_ip_literals(host: str) -> None:
    assert parse_ip_literal(host) is None


@pytest.mark.parametrize("host", ["256.0.0.1", "1.2.3.4.5", "1..2.3", "4294967296", "0x1.0x100.1"])
def test_rejects_malformed_numeric_hosts(host: str) -> None:
    with pytest.raises(SsrfError) as info:
        parse_ip_literal(host)
    assert info.value.code == "invalid_url"


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",  # loopback
        "10.1.2.3",  # RFC 1918
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",  # cloud metadata endpoint (link-local)
        "100.64.0.1",  # CGNAT
        "0.0.0.0",  # unspecified
        "224.0.0.1",  # multicast
        "240.0.0.1",  # reserved
        "255.255.255.255",  # broadcast
        "192.0.2.10",  # documentation range
        "::1",
        "::",
        "fc00::1",  # IPv6 unique local
        "fd12:3456::1",
        "fe80::1",  # IPv6 link-local
        "ff02::1",  # IPv6 multicast
        "::ffff:127.0.0.1",  # IPv4-mapped loopback
        "::ffff:169.254.169.254",
        "64:ff9b::7f00:1",  # NAT64 of 127.0.0.1
        "2002:7f00:1::",  # 6to4 of 127.0.0.1
        "2002:a00:1::",  # 6to4 of 10.0.0.1
    ],
)
def test_blocks_non_public_addresses(address: str) -> None:
    assert is_blocked_ip(ipaddress.ip_address(address))


@pytest.mark.parametrize(
    "address", ["8.8.8.8", "1.1.1.1", "93.184.215.14", "2606:4700:4700::1111", "::ffff:8.8.8.8"]
)
def test_allows_public_addresses(address: str) -> None:
    assert not is_blocked_ip(ipaddress.ip_address(address))


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("ftp://example.com/", "only http"),
        ("file:///etc/passwd", "only http"),
        ("javascript:alert(1)", "only http"),
        ("https://", "no host"),
        ("https://user:secret@example.com/", "username or password"),
        ("https://user@example.com/", "username or password"),
        ("http://example.com:8080/", "standard ports"),
        ("https://example.com:22/", "standard ports"),
        ("http://example.com:99999/", "not a valid URL"),
        ("http://256.1.1.1/", "invalid IPv4"),
    ],
)
def test_rejects_bad_user_urls(url: str, reason: str) -> None:
    with pytest.raises(SsrfError, match=reason) as info:
        validate_user_url(url, NO_ALLOWLIST)
    assert info.value.code == "invalid_url"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/",
        "http://example.com/landing?utm_source=fb",
        "https://example.com:443/",
    ],
)
def test_accepts_normal_user_urls(url: str) -> None:
    validate_user_url(url, NO_ALLOWLIST)


def test_allowlisted_hosts_may_use_any_port() -> None:
    validate_user_url("http://fixtures:8080/", SsrfPolicy(allow_hosts=frozenset({"fixtures"})))


async def test_blocks_a_host_if_any_of_its_addresses_is_private() -> None:
    resolver = StaticResolver({"sneaky.example": ["93.184.215.14", "10.0.0.5"]})
    with pytest.raises(SsrfError) as info:
        await resolve_public("sneaky.example", NO_ALLOWLIST, resolver)
    assert info.value.code == "ssrf_blocked"


async def test_ip_literals_are_checked_without_dns() -> None:
    resolver = StaticResolver({})  # any DNS lookup would fail
    with pytest.raises(SsrfError) as info:
        await check_user_url("http://2130706433/", NO_ALLOWLIST, resolver)
    assert info.value.code == "ssrf_blocked"


async def test_unresolvable_hosts_are_reported_as_dns_failures() -> None:
    with pytest.raises(SsrfError) as info:
        await check_user_url("https://nope.example/", NO_ALLOWLIST, StaticResolver({}))
    assert info.value.code == "dns_failure"


async def test_public_hosts_pass() -> None:
    resolver = StaticResolver({"shop.example": ["93.184.215.14"]})
    await check_user_url("https://shop.example/", NO_ALLOWLIST, resolver)


async def test_allowlist_is_exact_hostname_match() -> None:
    resolver = StaticResolver({"fixtures": ["172.18.0.5"], "evil-fixtures": ["172.18.0.5"]})
    policy = SsrfPolicy(allow_hosts=frozenset({"fixtures"}))
    await resolve_public("fixtures", policy, resolver)
    with pytest.raises(SsrfError):
        await resolve_public("evil-fixtures", policy, resolver)
