"""Local stand-ins for the Meta and Google tag servers, for tests and the local demo.

Tests must never contact the real internet, and the demo must never send fake hits to Meta
or Google. These Playwright routes answer the tracking hosts locally, before any network
access. The stand-in scripts behave like the real tags *as seen on the network*:

- fbevents.js: fbq('init', id) fetches connect.facebook.net/signals/config/<id>, and
  fbq('track', 'PageView') produces GET https://www.facebook.com/tr/?id=<id>&ev=PageView
- gtag.js: gtag('config', 'G-...') produces a GA4 /g/collect page_view beacon (POST);
  gtag('config', 'AW-123') produces a Google Ads viewthroughconversion hit; an 'event' with
  send_to 'AW-123/label' produces a googleadservices.com conversion hit.
- gtm.js?id=GTM-XYZ: by convention, a stub container holds one GA4 tag, G-XYZ. Like a real
  container, it loads gtag.js for that measurement id.

The captured requests then look like real tag traffic, so the checks can't tell the difference.
Only enabled in tests and when TRACKING_STUBS=true (local demo), and even then only for pages
on allowlisted dev hosts (PageCapturer.uses_tracking_stubs). Never in production.
"""

import json
import re
from urllib.parse import parse_qs, urlsplit

from playwright.async_api import BrowserContext, Route

TRACKING_HOSTS = re.compile(
    r"^https?://(?:[a-z0-9-]+\.)*(?:facebook\.net|facebook\.com|googletagmanager\.com|"
    r"google-analytics\.com|analytics\.google\.com|doubleclick\.net|googleadservices\.com)"
    r"(?::\d+)?/",
    re.IGNORECASE,
)

# Smallest valid GIF: what real pixel endpoints answer with.
PIXEL_GIF = bytes.fromhex(
    "47494638396101000100800000000000ffffff21f90401000000002c00000000010001000002024401003b"
)

FBEVENTS_JS = """
(function () {
  var fbq = window.fbq;
  if (!fbq || fbq.__tagmonitorStub) return;
  var pixels = [];
  var sent = 0;
  function hit(id, ev) {
    // Like the real script, every hit carries a timestamp and counter; without them the
    // browser's image cache would merge two identical beacons into one request.
    sent += 1;
    new Image().src = "https://www.facebook.com/tr/?id=" + encodeURIComponent(id) +
      "&ev=" + encodeURIComponent(ev) + "&dl=" + encodeURIComponent(location.href) +
      "&ts=" + Date.now() + "&n=" + sent;
  }
  function loadConfig(id) {
    // Like the real script: fetch the pixel's config, which reveals the pixel id on the
    // network even when no event is ever sent.
    var script = document.createElement("script");
    script.async = true;
    script.src = "https://connect.facebook.net/signals/config/" + encodeURIComponent(id) +
      "?v=2.9.0&r=stable";
    document.head.appendChild(script);
  }
  function handle(args) {
    var command = args[0];
    if (command === "init") {
      pixels.push(String(args[1]));
      loadConfig(String(args[1]));
    }
    else if (command === "track" || command === "trackCustom") {
      pixels.forEach(function (id) { hit(id, String(args[1])); });
    } else if (command === "trackSingle") hit(String(args[1]), String(args[2]));
  }
  fbq.callMethod = function () { handle(arguments); };
  fbq.__tagmonitorStub = true;
  var queued = fbq.queue || [];
  fbq.queue = [];
  for (var i = 0; i < queued.length; i++) handle(queued[i]);
})();
"""

GTAG_JS = """
(function () {
  if (window.__tagmonitorStubGtag) return;
  window.__tagmonitorStubGtag = true;
  var dataLayer = window.dataLayer = window.dataLayer || [];
  var ga4Ids = [];
  function adsId(id) { return String(id).replace(/^AW-/, "").split("/")[0]; }
  function ga4Hit(tid, eventName) {
    navigator.sendBeacon("https://region1.google-analytics.com/g/collect?v=2&tid=" +
      encodeURIComponent(tid) + "&en=" + encodeURIComponent(eventName) +
      "&dl=" + encodeURIComponent(location.href));
  }
  function handle(args) {
    if (!args || typeof args.length !== "number") return;  // GTM-style object pushes
    if (args[0] === "config") {
      var id = String(args[1]);
      if (id.indexOf("G-") === 0) {
        ga4Ids.push(id);
        if (!(args[2] && args[2].send_page_view === false)) ga4Hit(id, "page_view");
      } else if (id.indexOf("AW-") === 0) {
        new Image().src = "https://googleads.g.doubleclick.net/pagead/viewthroughconversion/" +
          adsId(id) + "/?random=" + Date.now();
      }
    } else if (args[0] === "event") {
      var params = args[2] || {};
      var sendTo = params.send_to ? String(params.send_to) : "";
      if (sendTo.indexOf("AW-") === 0) {
        new Image().src = "https://www.googleadservices.com/pagead/conversion/" +
          adsId(sendTo) + "/?label=" + encodeURIComponent(sendTo.split("/")[1] || "");
      } else {
        ga4Ids.forEach(function (tid) { ga4Hit(tid, String(args[1])); });
      }
    }
  }
  var originalPush = dataLayer.push;
  dataLayer.push = function () {
    var result = originalPush.apply(dataLayer, arguments);
    for (var i = 0; i < arguments.length; i++) handle(arguments[i]);
    return result;
  };
  for (var i = 0; i < dataLayer.length; i++) handle(dataLayer[i]);
})();
"""

GTM_JS_TEMPLATE = """
(function () {
  var containerId = __CONTAINER_ID__;
  var measurementId = "G-" + containerId.replace(/^GTM-/, "");
  window.dataLayer = window.dataLayer || [];
  function gtag() { window.dataLayer.push(arguments); }
  gtag("js", new Date());
  gtag("config", measurementId);
  var script = document.createElement("script");
  script.async = true;
  script.src = "https://www.googletagmanager.com/gtag/js?id=" + encodeURIComponent(measurementId);
  document.head.appendChild(script);
})();
"""


async def install_tracking_stubs(context: BrowserContext) -> None:
    await context.route(TRACKING_HOSTS, _answer)


async def _answer(route: Route) -> None:
    parts = urlsplit(route.request.url)
    host = (parts.hostname or "").lower()
    path = parts.path
    if host.endswith("facebook.net") and path.endswith("/fbevents.js"):
        await _javascript(route, FBEVENTS_JS)
    elif host.endswith("facebook.net") and path.startswith("/signals/config/"):
        await _javascript(route, "/* pixel config */")
    elif host.endswith("googletagmanager.com") and path == "/gtm.js":
        container_id = parse_qs(parts.query).get("id", ["GTM-UNKNOWN"])[0]
        await _javascript(
            route, GTM_JS_TEMPLATE.replace("__CONTAINER_ID__", json.dumps(container_id))
        )
    elif host.endswith("googletagmanager.com") and path == "/gtag/js":
        await _javascript(route, GTAG_JS)
    elif host.endswith(("facebook.com", "doubleclick.net", "googleadservices.com")):
        await route.fulfill(status=200, content_type="image/gif", body=PIXEL_GIF)
    else:
        # GA4 collection and anything else on these hosts: "accepted, nothing to say".
        await route.fulfill(status=204, body=b"")


async def _javascript(route: Route, source: str) -> None:
    await route.fulfill(status=200, content_type="application/javascript", body=source)
