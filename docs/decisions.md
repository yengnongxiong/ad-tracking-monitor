# Design decisions

Each entry follows Context / Decision / Alternatives considered / Consequences. Entries are numbered in the order they were decided; the milestone that introduced each is noted.

---

## ADR-001: Run MinIO from a maintained community image (M0)

**Context.** The PRD uses MinIO as local S3-compatible storage. During setup, `docker pull minio/minio` failed with "repository does not exist": MinIO stopped distributing its community server image on Docker Hub. A frozen 2025 copy remains on quay.io, but it gets no fixes.

**Decision.** Use `pgsty/minio`, a community rebuild of the same open-source MinIO server, pinned to `RELEASE.2026-08-04T00-00-00Z`, with amd64 and arm64 images. It takes the same `server /data` command and serves the same S3 API.

**Alternatives considered.**
- `quay.io/minio/minio` pinned to its last release: official, but frozen with no security fixes.
- A different S3 server (SeaweedFS, Garage, RustFS): changes the PRD's stack for no functional gain.
- Mocking S3 in tests (moto): the project rules require tests against real MinIO.

**Consequences.** The app only talks S3 through boto3 with an endpoint URL, so switching images later is a one-line change in `docker-compose.yml`. The trust we place in a community rebuild is acceptable for local development. Production uses a managed S3-compatible bucket.

---

## ADR-002: One container image for the API and the worker (M0)

**Context.** The worker needs Chromium and its system libraries. The PRD makes the official Playwright Python image the worker's base. The API needs none of that.

**Decision.** Build one image from `mcr.microsoft.com/playwright/python:v1.63.0-noble` and run it with different commands (`uvicorn …` for the API, `python -m tagmonitor.worker` for workers). The Playwright Python package in `uv.lock` is pinned to the image's version, so the bundled browser always matches. A `dev` build stage adds pytest, ruff and mypy.

**Alternatives considered.** A separate slim `python:3.12-slim` image for the API. That's smaller and has less attack surface, but it means two base images, two dependency installs, and a second place where versions can drift.

**Consequences.** The API image is larger than it needs to be (it carries a browser). That's fine for this project's scale. If image size or cold starts ever matter, add a slim `api` build stage; the code needs no changes.

---

## ADR-003: Same-origin proxy for the API (M0)

**Context.** The dashboard (Next.js) and the API (FastAPI) are separate servers. Auth uses an httpOnly session cookie. If the browser called the API on another origin, the cookie would be third-party: `SameSite=Lax` wouldn't send it on cross-site `fetch`, and it would need CORS with credentials, `SameSite=None` and `Secure`, all harder to get right.

**Decision.** Next.js rewrites `/api/*` to FastAPI (`web/next.config.ts`). The browser only ever talks to the web origin, so the session cookie is first-party and plain `SameSite=Lax` works. FastAPI serves everything under `/api` so no path rewriting is needed.

**Alternatives considered.**
- CORS with credentials between two origins: more configuration, and cross-site cookies are increasingly blocked by browsers.
- Bearer tokens in `localStorage`: readable by any XSS on the page, unlike an httpOnly cookie.
- Calling FastAPI only from Next.js server code: works, but duplicates every endpoint as a Next route.

**Consequences.** Every API request takes one extra hop through the Next.js server. FastAPI sees the Next.js server as the client, so anything that needs the real client IP (the login rate limit, M6) must read `X-Forwarded-For` and trust it only from the web tier. In production builds (`next build`), the rewrite destination is fixed at build time, so `API_INTERNAL_URL` must be set when building.

---

## ADR-004: Enforce the SSRF guard in a per-capture egress proxy, not only in `context.route` (M2)

**Context.** Users give us URLs, and our browser loads them from inside our network. A hostile page could make it request internal addresses: the cloud metadata service (`169.254.169.254`), the database, the admin API. The PRD asks for a guard on "every navigation, every redirect hop, and every subresource request (via `context.route`)". Before building on that, I tested it. With Playwright 1.63, a `context.route("**/*")` handler saw `GET /a` but **not** the `302 → /b → /c` hops that followed, even though the browser loaded all three. Playwright follows redirects internally and doesn't give the handler a chance to see them. A route-based guard would therefore let `https://evil.example/ → 302 → http://169.254.169.254/` through.

**Decision.** Every capture starts a tiny asyncio HTTP proxy on `127.0.0.1` (`browser/egress_proxy.py`), and the browser context is configured to send **all** traffic through it. Playwright also forces loopback traffic through a configured proxy, which is exactly what we want. For each connection (`CONNECT host:port` for HTTPS, absolute-form `GET http://…` for plain HTTP), the proxy:
1. parses the host the way a browser would, including the decimal, hex and octal IPv4 forms such as `http://2130706433/`;
2. resolves it and refuses the connection if **any** address is non-public (private, loopback, link-local, CGNAT, multicast, reserved, unspecified, IPv6 ULA and link-local, or IPv4 embedded in IPv6 via mapping, NAT64, 6to4 or Teredo);
3. connects to the **same IP it checked**.

The pure rules live in `browser/ssrf.py` and are reused by the API (M6) to reject bad URLs when a site is added. A Chromium flag forces WebRTC traffic through the proxy too (otherwise WebRTC can open UDP connections that skip it), and service workers are blocked. `context.route` is still used for the tracking stubs, which answer before any network access.

**Alternatives considered.**
- *Route handler with `route.fetch(max_redirects=0)` and fulfill.* Every hop would come back through the handler, but all traffic would then flow through Playwright's own HTTP client instead of Chrome's network stack: throttling wouldn't apply, timings and LCP would be distorted, and CORS preflights and WebSockets still wouldn't be covered.
- *Route handler only (the PRD's wording):* doesn't see redirect hops, as the experiment showed.
- *A network-level firewall only:* the right defense in depth, but it lives in infrastructure, not in the code the project can test.

**Consequences.**
- One enforcement point that covers navigations, every redirect hop, subresources, iframes, WebSockets and CORS preflights, and that is unit-testable over raw sockets (`tests/test_egress_proxy.py`).
- Because the proxy connects to the address it validated, a DNS answer can't change between the check and the connection (DNS rebinding) for browser traffic. **What's left:** the API's check when a site is added and the later capture are separate lookups, which is harmless because the capture checks again. Anything outside the browser that fetches user URLs (robots.txt in M8) must use the same `resolve_public` check and connect to the address it validated. As defense in depth, **run workers on a network segment with no route to internal services** (a separate subnet or VPC with egress only to the internet, no access to the metadata endpoint).
- Plain-HTTP requests are forwarded with `Connection: close`, so each proxy connection carries exactly one request. That costs extra TCP handshakes on `http://` pages, which are rare and mostly redirect to HTTPS.
- A redirect to a private address becomes a navigation with `error_code = "ssrf_blocked"`. The worker (M4) treats it as a permanent failure, as §12 requires.
- The proxy is about 200 lines of protocol code we own. It's tested for allowed and blocked plain HTTP, CONNECT tunnels, obfuscated IP literals, DNS and connection failures, and per-capture DNS caching.

---

## ADR-005: Capture once, analyze many (M2)

**Context.** Loading a page in a real browser is slow (seconds), flaky (networks, third-party scripts) and expensive. Deciding whether a pixel fired is pure logic. If the two are mixed, every check test needs a browser and every logic change needs a re-crawl.

**Decision.** The browser step produces one serializable `PageCapture` (`tagmonitor/page_capture.py`): every request (URL, method, type, status, failure, timing, size, and POST bodies for tracking endpoints only), the navigation outcome and redirect chain, console and page errors, DOM facts, performance metrics, and the connections the SSRF proxy refused. Every check (M3) is a pure function of `PageCapture` plus the site's settings. The screenshot is stored beside it, not inside it. The model carries `capture_schema_version`.

**Alternatives considered.** Running checks live against the Playwright page: this couples logic to a browser session, makes unit tests slow, and makes it impossible to re-run a new check on old data.

**Consequences.** Checks are unit-tested from saved JSON in milliseconds (`tests/fixtures/captures/`, M3). Stored captures can be re-analyzed when detection rules change, which matters because tracking endpoints drift over time. The cost: anything a check needs must be captured up front, so adding a new signal means changing the capture schema (bump the version) and waiting for new captures.

---

## ADR-007: UUIDs for rows that appear in URLs, bigint identity for high-volume rows (M1)

**Context.** The PRD fixes `jobs.id` as `bigserial` and leaves the other id types open. Ids appear in API paths (`/api/sites/{id}`) and in the database's hottest indexes.

**Decision.** `users`, `sessions`, `sites` and `scans` use `uuid` (`gen_random_uuid()`, built into Postgres 13+). `check_runs`, `check_results`, `alerts`, `scan_targets` and `llm_usage` use `bigint GENERATED ALWAYS AS IDENTITY`. `jobs` keeps the PRD's `bigserial`.

**Alternatives considered.**
- UUIDs everywhere: uniform, but random UUIDs make large indexes on append-heavy tables bigger and scatter inserts across pages.
- Integers everywhere: compact, but `/api/sites/42` invites guessing neighbouring ids. Every query is scoped by `user_id` anyway (the real IDOR defense), so this is defense in depth, not the defense.

**Consequences.** Sites and users can't be enumerated from URLs. Run and result ids are guessable, but every query that reads them joins through `sites.user_id`, and the M6 IDOR tests check exactly that.
