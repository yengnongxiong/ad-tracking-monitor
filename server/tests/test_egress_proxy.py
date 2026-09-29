"""The egress proxy, driven with raw HTTP so each rule is tested without a browser."""

import asyncio

from tagmonitor.browser.egress_proxy import PROXY_ERROR_HEADER, EgressProxy
from tagmonitor.browser.ssrf import IPAddress
from tests.conftest import TEST_POLICY, TEST_RESOLVER
from tests.fixture_server import FIXTURE_HOST, FixtureServer, StaticResolver


async def send(proxy: EgressProxy, raw: bytes) -> bytes:
    """Send one raw request to the proxy and read until it closes the connection."""
    host, port = proxy.url.removeprefix("http://").split(":")
    reader, writer = await asyncio.open_connection(host, int(port))
    writer.write(raw)
    await writer.drain()
    response = await asyncio.wait_for(reader.read(), 10)
    writer.close()
    return response


def get(url: str) -> bytes:
    return f"GET {url} HTTP/1.1\r\nHost: ignored\r\nProxy-Connection: keep-alive\r\n\r\n".encode()


async def test_forwards_plain_http_to_allowed_hosts(fixture_server: FixtureServer) -> None:
    async with EgressProxy(TEST_POLICY, TEST_RESOLVER) as proxy:
        response = await send(proxy, get(fixture_server.url("no_tags")))

    assert response.startswith(b"HTTP/1.0 200")
    assert b"Connection: close" in response  # one request per proxy connection
    assert b"<h1>Fresh coffee" in response
    assert proxy.failures == []


async def test_refuses_loopback_and_never_connects(fixture_server: FixtureServer) -> None:
    fixture_server.hits.clear()
    async with EgressProxy(TEST_POLICY, TEST_RESOLVER) as proxy:
        response = await send(proxy, get(f"http://127.0.0.1:{fixture_server.port}/internal/secret"))

    assert response.startswith(b"HTTP/1.1 403")
    assert f"{PROXY_ERROR_HEADER}: ssrf_blocked".encode() in response
    assert "/internal/secret" not in fixture_server.hits
    assert [(f.host, f.code) for f in proxy.failures] == [("127.0.0.1", "ssrf_blocked")]


async def test_refuses_obfuscated_ip_literals(fixture_server: FixtureServer) -> None:
    fixture_server.hits.clear()
    async with EgressProxy(TEST_POLICY, TEST_RESOLVER) as proxy:
        for host in ("2130706433", "0x7f.1", "[::ffff:127.0.0.1]"):
            response = await send(
                proxy, get(f"http://{host}:{fixture_server.port}/internal/secret")
            )
            assert response.startswith(b"HTTP/1.1 403"), host
    assert fixture_server.hits == []


async def test_refuses_https_tunnels_to_the_metadata_endpoint() -> None:
    async with EgressProxy(TEST_POLICY, TEST_RESOLVER) as proxy:
        response = await send(proxy, b"CONNECT 169.254.169.254:443 HTTP/1.1\r\n\r\n")
    assert response.startswith(b"HTTP/1.1 403")
    assert proxy.failures[0].code == "ssrf_blocked"


async def test_tunnels_to_allowed_hosts(fixture_server: FixtureServer) -> None:
    async with EgressProxy(TEST_POLICY, TEST_RESOLVER) as proxy:
        host, port = proxy.url.removeprefix("http://").split(":")
        reader, writer = await asyncio.open_connection(host, int(port))
        writer.write(f"CONNECT {FIXTURE_HOST}:{fixture_server.port} HTTP/1.1\r\n\r\n".encode())
        assert await reader.readuntil(b"\r\n\r\n") == b"HTTP/1.1 200 Connection Established\r\n\r\n"
        # The tunnel is a byte pipe: speak HTTP to the fixture server straight through it.
        writer.write(b"GET /no_tags/ HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        response = await asyncio.wait_for(reader.read(), 10)
        writer.close()
    assert response.startswith(b"HTTP/1.0 200")


async def test_unknown_hosts_fail_as_dns_errors_without_reaching_the_internet() -> None:
    async with EgressProxy(TEST_POLICY, TEST_RESOLVER) as proxy:
        response = await send(proxy, b"CONNECT www.google.com:443 HTTP/1.1\r\n\r\n")
    assert response.startswith(b"HTTP/1.1 502")
    assert proxy.failures[0].code == "dns_failure"


async def test_reports_connection_failures() -> None:
    async with EgressProxy(TEST_POLICY, TEST_RESOLVER) as proxy:
        response = await send(proxy, get(f"http://{FIXTURE_HOST}:1/"))  # nothing listens on 1
    assert response.startswith(b"HTTP/1.1 502")
    assert proxy.failures[0].code == "connection_failed"


async def test_tries_every_validated_address(fixture_server: FixtureServer) -> None:
    """Regression: only the first DNS answer was tried, so a site whose first address was
    unreachable (typically IPv6 on a host without IPv6) looked down. The fixture server only
    listens on 127.0.0.1, so ::1 refuses the connection."""
    resolver = StaticResolver({FIXTURE_HOST: ["::1", "127.0.0.1"]})
    async with EgressProxy(TEST_POLICY, resolver) as proxy:
        response = await send(proxy, get(fixture_server.url("no_tags")))
    assert response.startswith(b"HTTP/1.0 200")
    assert proxy.failures == []


async def test_resolves_each_host_once_per_capture(fixture_server: FixtureServer) -> None:
    class CountingResolver(StaticResolver):
        calls = 0

        async def resolve(self, host: str) -> list[IPAddress]:
            self.calls += 1
            return await super().resolve(host)

    resolver = CountingResolver({FIXTURE_HOST: ["127.0.0.1"]})
    async with EgressProxy(TEST_POLICY, resolver) as proxy:
        await send(proxy, get(fixture_server.url("no_tags")))
        await send(proxy, get(fixture_server.url("ga4_ok")))
    assert resolver.calls == 1
