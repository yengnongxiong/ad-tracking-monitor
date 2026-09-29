"""A per-capture HTTP proxy that applies the SSRF policy to every browser connection.

Why a proxy (ADR-004): Playwright's context.route() is never called for redirect hops, so a
public page that redirects to http://127.0.0.1/ would get past a route-based check. Forcing
the browser context through this proxy means every connection is checked first: navigations,
each redirect hop, subresources, iframes, WebSockets, CORS preflights. The proxy then
connects only to the addresses it validated (trying each in turn, like a browser), so DNS
can't give a different answer between the check and the connection (DNS rebinding), at least
for browser traffic.

It speaks only what Chromium needs from a proxy:
- CONNECT host:port: a byte tunnel, used for HTTPS and wss. We see the host, never the content.
- GET http://host/... (absolute-form): plain HTTP. We forward it with "Connection: close" so a
  proxy connection carries exactly one request and can't be reused for a different host.

One instance per capture: the DNS cache and the failure log are scoped to that page load.
"""

import asyncio
import contextlib
from dataclasses import dataclass
from types import TracebackType
from typing import Self
from urllib.parse import urlsplit

from tagmonitor.browser.ssrf import (
    IPAddress,
    Resolver,
    SsrfError,
    SsrfPolicy,
    normalize_host,
    resolve_public,
)

# Header the proxy adds to responses it generates itself, so the capture can tell our 403/502
# apart from one the website sent.
PROXY_ERROR_HEADER = "X-Tagmonitor-Proxy-Error"

MAX_HEAD_BYTES = 64 * 1024
HEAD_TIMEOUT_S = 30.0
CONNECT_TIMEOUT_S = 10.0
_HOP_BY_HOP = (b"connection:", b"keep-alive:", b"proxy-connection:", b"proxy-authorization:")


@dataclass(frozen=True)
class ProxyFailure:
    """A connection the proxy refused or couldn't make."""

    host: str
    port: int
    code: str  # ssrf_blocked | dns_failure | connection_failed | invalid_url
    detail: str


class EgressProxy:
    def __init__(self, policy: SsrfPolicy, resolver: Resolver) -> None:
        self.policy = policy
        self.resolver = resolver
        self.failures: list[ProxyFailure] = []
        self._resolved: dict[str, list[IPAddress] | SsrfError] = {}
        self._server: asyncio.Server | None = None
        self._connections: set[asyncio.Task[object]] = set()

    @property
    def url(self) -> str:
        assert self._server is not None, "use `async with EgressProxy(...)`"
        port = self._server.sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}"

    async def __aenter__(self) -> Self:
        self._server = await asyncio.start_server(
            self._on_connection, "127.0.0.1", 0, limit=MAX_HEAD_BYTES
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        assert self._server is not None
        self._server.close()
        # Idle keep-alive tunnels would otherwise live on after the browser context is gone.
        for task in self._connections:
            task.cancel()
        await asyncio.gather(*self._connections, return_exceptions=True)
        await self._server.wait_closed()

    # -- policy -----------------------------------------------------------------------------

    def _record(self, host: str, port: int, error: SsrfError) -> None:
        failure = ProxyFailure(host, port, error.code, str(error))
        if failure not in self.failures:
            self.failures.append(failure)

    async def _authorize(self, host: str, port: int) -> list[IPAddress]:
        """Return the addresses we may connect to, or raise SsrfError. Cached per capture."""
        key = normalize_host(host)
        if key not in self._resolved:
            try:
                self._resolved[key] = await resolve_public(key, self.policy, self.resolver)
            except SsrfError as exc:
                self._resolved[key] = exc
        result = self._resolved[key]
        if isinstance(result, SsrfError):
            self._record(key, port, result)
            raise result
        return result

    async def _open(
        self, host: str, port: int
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        # Every address passed the policy, so any of them is safe. Hosts often list several
        # (IPv6 and IPv4, or a few servers); one that's unreachable mustn't make the site look
        # down, so try them in order until one answers.
        problems = []
        for address in await self._authorize(host, port):
            try:
                return await asyncio.wait_for(
                    asyncio.open_connection(str(address), port), CONNECT_TIMEOUT_S
                )
            except (OSError, TimeoutError) as exc:
                problems.append(f"{address}: {exc or type(exc).__name__}")
        error = SsrfError(
            "connection_failed", f"could not connect to {host}:{port} ({'; '.join(problems)})"
        )
        self._record(normalize_host(host), port, error)
        raise error

    # -- protocol ---------------------------------------------------------------------------

    async def _on_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        task = asyncio.current_task()
        assert task is not None
        self._connections.add(task)
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), HEAD_TIMEOUT_S)
            request_line, _, header_block = head.partition(b"\r\n")
            method, target, version = request_line.decode("latin-1").split(" ", 2)
            if method == "CONNECT":
                await self._tunnel(target, reader, writer)
            else:
                await self._forward(method, target, version, header_block, reader, writer)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError):
            pass  # malformed or truncated request: just drop the connection
        except (TimeoutError, ConnectionError):
            pass
        finally:
            writer.close()
            self._connections.discard(task)

    async def _tunnel(
        self, target: str, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        parts = urlsplit(f"//{target}")
        if not parts.hostname:
            raise ValueError("CONNECT without a host")
        try:
            up_reader, up_writer = await self._open(parts.hostname, parts.port or 443)
        except SsrfError as exc:
            writer.write(_error_response(exc))
            await writer.drain()
            return
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        try:
            await asyncio.gather(_copy(reader, up_writer), _copy(up_reader, writer))
        finally:
            up_writer.close()

    async def _forward(
        self,
        method: str,
        target: str,
        version: str,
        header_block: bytes,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        parts = urlsplit(target)
        if parts.scheme != "http" or not parts.hostname:
            raise ValueError("expected an absolute http:// URL")
        try:
            up_reader, up_writer = await self._open(parts.hostname, parts.port or 80)
        except SsrfError as exc:
            writer.write(_error_response(exc))
            await writer.drain()
            return

        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        headers = [line for line in header_block.split(b"\r\n") if line]
        up_writer.write(
            f"{method} {path} {version}\r\n".encode("latin-1") + _with_close(headers) + b"\r\n"
        )
        # Stream any request body upstream while the response streams back.
        request_body = asyncio.create_task(_copy(reader, up_writer))
        try:
            status_and_headers = await up_reader.readuntil(b"\r\n\r\n")
            status_line, _, response_headers = status_and_headers.partition(b"\r\n")
            lines = [line for line in response_headers.split(b"\r\n") if line]
            writer.write(status_line + b"\r\n" + _with_close(lines) + b"\r\n")
            await _copy(up_reader, writer)
        finally:
            request_body.cancel()
            up_writer.close()


def _with_close(header_lines: list[bytes]) -> bytes:
    """Drop hop-by-hop headers and add Connection: close (one request per connection)."""
    kept = [line for line in header_lines if not line.lower().startswith(_HOP_BY_HOP)]
    return b"".join(line + b"\r\n" for line in [*kept, b"Connection: close"])


async def _copy(source: asyncio.StreamReader, sink: asyncio.StreamWriter) -> None:
    """Copy bytes until EOF, then pass the EOF on so the other side sees the close."""
    try:
        while chunk := await source.read(65536):
            sink.write(chunk)
            await sink.drain()
    except ConnectionError:
        return
    if sink.can_write_eof() and not sink.is_closing():
        with contextlib.suppress(OSError):
            sink.write_eof()


def _error_response(error: SsrfError) -> bytes:
    status = "403 Forbidden" if error.code == "ssrf_blocked" else "502 Bad Gateway"
    body = f"tag-monitor egress proxy refused this request: {error.code}\n".encode()
    head = (
        f"HTTP/1.1 {status}\r\n"
        f"{PROXY_ERROR_HEADER}: {error.code}\r\n"
        "Content-Type: text/plain\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    )
    return head.encode() + body
