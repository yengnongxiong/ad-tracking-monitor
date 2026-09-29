"""The demo landing page for the local stack (M7).

Serves "Bean There Coffee", a landing page with a Meta Pixel and GA4, at http://beanthere.demo/
inside docker compose and at http://localhost:8088 on your machine. While the file
demo/state/pixel-broken exists, the page still loads the Meta Pixel but never sends PageView,
the classic "installed but not firing" breakage. Toggle it with:

    make demo-break-pixel
    make demo-fix-pixel

Standard library only, so it runs in any Python container.
"""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).parent
PAGE = HERE / "site" / "index.html"
BROKEN_FLAG = HERE / "state" / "pixel-broken"


class DemoHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path.split("?")[0] not in ("/", "/index.html"):
            self.send_error(404)
            return
        pageview = "" if BROKEN_FLAG.exists() else "fbq('track', 'PageView');"
        body = PAGE.read_text().replace("/* PAGEVIEW */", pageview).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8088)
    port = parser.parse_args().port
    print(f"demo site on :{port} (pixel {'broken' if BROKEN_FLAG.exists() else 'working'})")
    ThreadingHTTPServer(("0.0.0.0", port), DemoHandler).serve_forever()  # noqa: S104
