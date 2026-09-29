"""A tiny local web server for the fixture sites in tests/fixtures/sites.

Static files are served as-is. A few paths have scripted behavior that a static server can't
express: a slow hero image, a 500 page, a three-hop redirect chain, and a redirect to a
private IP (the SSRF guard must stop the browser from following it).
"""

import ipaddress
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tagmonitor.browser.ssrf import IPAddress, SsrfError, normalize_host

SITES_DIR = Path(__file__).parent / "fixtures" / "sites"
FIXTURE_HOST = "fixtures.test"
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".svg": "image/svg+xml"}


class FixtureServer:
    def __init__(self) -> None:
        self.hits: list[str] = []  # every path requested, in order
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                pass  # keep test output quiet

            def do_GET(self) -> None:
                server.hits.append(self.path)
                server.handle(self)

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    def url(self, site: str) -> str:
        return f"http://{FIXTURE_HOST}:{self.port}/{site}/"

    ROBOTS_TXT = (
        b"User-agent: *\nDisallow: /private-area/\n\n"
        b"User-agent: tag-monitor\nDisallow: /private-area/\nDisallow: /no-tagmonitor/\n"
    )

    def handle(self, request: BaseHTTPRequestHandler) -> None:
        path = request.path.split("?")[0]
        host = request.headers.get("Host", "").split(":")[0]
        if path == "/robots.txt":
            # Scan tests use hostnames to pick a robots.txt behavior.
            if host.startswith("no-robots."):
                self._send(request, 404, b"not found", "text/plain")
            elif host.startswith("robots-500."):
                self._send(request, 500, b"oops", "text/plain")
            else:
                self._send(request, 200, self.ROBOTS_TXT, "text/plain")
            return
        redirects = {
            "/redirect_chain_3/": "/redirect_chain_3/hop1",
            "/redirect_chain_3/hop1": "/redirect_chain_3/hop2",
            "/redirect_chain_3/hop2": "/redirect_chain_3/final/",
            "/redirect_to_private_ip/": f"http://127.0.0.1:{self.port}/internal/secret",
        }
        if path in redirects:
            request.send_response(302)
            request.send_header("Location", redirects[path])
            request.end_headers()
        elif path == "/redirect_chain_3/final/":
            self._send_file(request, SITES_DIR / "redirect_chain_3" / "final.html")
        elif path == "/internal/secret":
            # Stands in for an internal service. Reaching it means the SSRF guard failed.
            self._send(request, 200, b"internal secret", "text/plain")
        elif path == "/http_500/":
            self._send_file(request, SITES_DIR / "http_500" / "index.html", status=500)
        elif path == "/slow_lcp/hero.svg":
            time.sleep(5)
            self._send_file(request, SITES_DIR / "slow_lcp" / "hero.svg")
        else:
            file = (SITES_DIR / path.lstrip("/")).resolve()
            if file.is_dir():
                file = file / "index.html"
            if SITES_DIR.resolve() in file.parents and file.is_file():
                self._send_file(request, file)
            else:
                self._send(request, 404, b"not found", "text/plain")

    def _send_file(self, request: BaseHTTPRequestHandler, file: Path, status: int = 200) -> None:
        content_type = CONTENT_TYPES.get(file.suffix, "application/octet-stream")
        self._send(request, status, file.read_bytes(), content_type)

    @staticmethod
    def _send(request: BaseHTTPRequestHandler, status: int, body: bytes, content_type: str) -> None:
        request.send_response(status)
        request.send_header("Content-Type", content_type)
        request.send_header("Content-Length", str(len(body)))
        request.end_headers()
        request.wfile.write(body)


class StaticResolver:
    """A resolver with a fixed table, so tests never make real DNS queries.

    Unknown names fail like NXDOMAIN, which also means any accidental request to a real
    website is refused by the egress proxy instead of reaching the internet.
    """

    def __init__(self, table: dict[str, list[str]]) -> None:
        self.table = {
            name: [ipaddress.ip_address(address) for address in addresses]
            for name, addresses in table.items()
        }

    async def resolve(self, host: str) -> list[IPAddress]:
        addresses = self.table.get(normalize_host(host))
        if not addresses:
            raise SsrfError("dns_failure", f"{host} is not in the test DNS table")
        return addresses
