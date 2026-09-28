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

## ADR-006: Website failures are data; only our own failures are job failures (M4)

**Context.** §12 lists "timeout, DNS failure, connection reset, 5xx" as transient job failures to retry, and §10 says the same things make Page health fail. If a down website made the job retry and then die, no result would ever be saved, the alert state machine would never see a failure, and **a site that's down would never alert**, which is the most important alert of all.

**Decision.** Two kinds of failure, handled in different places:
- **The website failed** (DNS, timeout, refused connection, HTTP 5xx): the capture succeeds *at observing a failure*. It's recorded in the PageCapture (`navigation.error_code`), saved as a completed run, and Page health reports it. The other checks say "not evaluated", so one outage produces one alert. Flakiness is handled by the confirmation re-check (M5), not by job retries.
- **Our machinery failed** (browser crash, 60 s capture budget exceeded, storage or database errors): the job fails *transiently* and retries with backoff. After `max_attempts` it's dead, and an error run records why.
- **Permanent:** an invalid URL, or a URL that resolves or redirects to a private address (`ssrf_blocked`), goes dead immediately, as §12 says, with an error run for the UI.

**Alternatives considered.** Retrying website failures as the PRD's wording suggests: it hides outages behind retries, then drops them.

**Consequences.** A down site produces a result within one job, and the alert follows the normal suspect → confirm → alert path. Job retries stay reserved for things a retry can actually fix.

---

## ADR-007: UUIDs for rows that appear in URLs, bigint identity for high-volume rows (M1)

**Context.** The PRD fixes `jobs.id` as `bigserial` and leaves the other id types open. Ids appear in API paths (`/api/sites/{id}`) and in the database's hottest indexes.

**Decision.** `users`, `sessions`, `sites` and `scans` use `uuid` (`gen_random_uuid()`, built into Postgres 13+). `check_runs`, `check_results`, `alerts`, `scan_targets` and `llm_usage` use `bigint GENERATED ALWAYS AS IDENTITY`. `jobs` keeps the PRD's `bigserial`.

**Alternatives considered.**
- UUIDs everywhere: uniform, but random UUIDs make large indexes on append-heavy tables bigger and scatter inserts across pages.
- Integers everywhere: compact, but `/api/sites/42` invites guessing neighbouring ids. Every query is scoped by `user_id` anyway (the real IDOR defense), so this is defense in depth, not the defense.

**Consequences.** Sites and users can't be enumerated from URLs. Run and result ids are guessable, but every query that reads them joins through `sites.user_id`, and the M6 IDOR tests check exactly that.

---

## ADR-008: Detect tags by observing network requests, not by searching the HTML (M3)

**Context.** The easy way to "check for a Meta Pixel" is to search the page source for `fbq(` or a pixel id. That answers "is the code there?", not "does it work?", and the second question is the one that costs money. Tags break in ways the HTML doesn't show: a consent tool that never lets them run, a JavaScript error earlier on the page, a script blocked by a Content-Security-Policy, a Tag Manager container that wasn't published, a snippet that sets the pixel up but never tracks, or the pixel installed twice.

**Decision.** Load the page in a real browser, record every request it makes (M2), and decide from the traffic: did `fbevents.js` load, did a `facebook.com/tr?…ev=PageView` hit leave the browser, and how many times? Same for gtag.js/GTM and the GA4 `/g/collect` and Google Ads endpoints. The patterns live in `checks/tracking_patterns.py` with unit tests on real-shaped URLs and POST bodies (GET beacons, urlencoded and multipart POSTs, batched GA4 events). The verdict logic shared by the three tag checks is one ordered question list (`checks/tag_verdict.py`), so the statuses mean the same thing for every vendor.

**Alternatives considered.**
- HTML/regex search: fast and simple, but it misses exactly the failures we exist to catch, and it's fooled by tags injected at runtime (GTM).
- Reading the tags' JavaScript globals (`window.fbq`, `dataLayer`): closer, but a global can exist while nothing is sent, and it ties us to each vendor's internals.

**Consequences.**
- "Installed but not firing", "script blocked" and "duplicate PageView" are detectable at all, which is the point of the product.
- The endpoints are undocumented and change over time. Mitigations: patterns in one module, unit tests, and `python -m tagmonitor.verify_patterns <urls>`, which lists requests to tag hosts that matched no pattern (how a moved endpoint shows up).
- Tags we can't see from a browser stay invisible: the Conversions API (server-side), and GA4 sent through a first-party server-side container on the site's own domain. Both are documented limitations.
- A hit counts as "sent" when it left the browser (no network failure), whatever the vendor answered: the vendor received it.

---

## ADR-009: Google tags are three checks (GA4, Ads, GTM), not one check with sub-results (M3)

**Context.** The PRD describes one GoogleTagCheck that "returns separate sub-results for GA4, Google Ads, and GTM". But results and alert state are keyed by a single `check_key` (§11, §13), so one check could only raise one alert for any Google problem, and a second problem while the first was still alerting would go unnoticed.

**Decision.** Three check classes with three keys (`google_ga4`, `google_ads`, `google_gtm`) in one module (`checks/google_tags.py`), sharing the parsers. Each has its own alert state and email ("Your GA4 tag stopped firing"). The dashboard's single "Google" status dot (§18) shows the worst of the three.

**Alternatives considered.** One check whose `details` hold three sub-results: matches the PRD's wording, but blurs alerts ("something Google broke") and hides a second failure behind the first.

**Consequences.** Seven check keys instead of five. GTM has no "expected IDs" field in the schema, so a missing container is only `info`; if the owner set expected GA4 IDs, the GA4 check still fails when a missing container takes GA4 down with it. A `GT-` "Google tag" can route to GA4 or Ads destinations we can't see, so it's attributed by the ids its hits carry, never assumed.


---

## ADR-010: The job queue lives in Postgres (M4)

**Context.** Workers need a queue with priorities, delayed retries, deduplication, crash recovery and horizontal scaling. The usual answer is Redis plus Celery, RQ or Sidekiq.

**Decision.** A `jobs` table and about 200 lines in `queue/jobs.py`:
- **Claiming** is PRD §12's single `UPDATE … WHERE id IN (SELECT … FOR UPDATE SKIP LOCKED LIMIT n) RETURNING *`. SKIP LOCKED makes concurrent claimers skip rows another transaction holds instead of waiting, so no job goes out twice and nobody blocks. A test drains 400 jobs with 20 concurrent claimers and checks every job was claimed exactly once.
- **Leases with fencing.** A claimed job is leased to `(id, locked_by, attempts)`, and every later write (complete, fail, release, heartbeat) must match that whole token. If a worker stalls long enough for the reaper to requeue its job and another worker claims it, the stale worker's late `complete()` updates zero rows and raises `LostLease`. Because completion shares a transaction with saving results, the stale results roll back too.
- **Retries:** transient failures return to `queued` with `min(30 s × 2^(attempts−1), 30 min) ± 20%` backoff. Permanent failures, or running out of attempts, mean `dead`. `last_error` is always kept.
- **Crash recovery:** a heartbeat every 15 s; the reaper requeues jobs silent for 2 minutes. The crashed attempt counts, so a "poison pill" job that kills its worker ends up dead instead of taking down the fleet.
- **Dedupe:** a partial unique index on `dedupe_key WHERE status IN ('queued','running')` means one active capture per site, enforced by the database.

**Alternatives considered.**
- Redis + Celery/RQ: very fast, but a second datastore to run and back up, and job state lives apart from the data it's about. Enqueuing a job in the same transaction as the data that caused it (the outbox in M5, the confirm re-check) would need two-phase tricks.
- A managed queue (SQS, Cloud Tasks): no local story without emulators, and it has the same transaction split.

**Consequences.** One datastore; jobs enqueue atomically with business data; queue state is plain SQL for debugging and the ops page. The limit is throughput: Postgres queues handle thousands of jobs per second, and our jobs take seconds each (browser time), so the database is nowhere near the bottleneck at this scale (M10 benchmarks this). Polling every second when idle costs a trivial query; LISTEN/NOTIFY could make pickup instant if that ever matters.

---

## ADR-011: Advisory locks for the scheduler, the reaper, and per-domain politeness (M4)

**Context.** Every worker runs the scheduler and reaper loops (so there's no single "scheduler box" to keep alive), but only one should tick at a time. Separately, two workers must never load pages from the same website at once (politeness, and not getting our IPs blocked).

**Decision.**
- Scheduler and reaper ticks take a **transaction-scoped** `pg_try_advisory_xact_lock(7311, n)`. Whoever gets it ticks; the others skip. It's released automatically at commit or rollback, so a crashed worker can't hold it. Correctness doesn't depend on the lock: the due-sites query uses SKIP LOCKED and the insert is deduplicated, so the lock only saves wasted work.
- Captures take a **session-scoped** `pg_try_advisory_lock(hashtextextended(registrable_domain, 0))` on a connection held for the whole capture (both devices). If it's taken, the job is released for 20 s **without using an attempt**, since being polite isn't a failure. The pool runs in autocommit mode, so the connection holding the lock is idle, not "idle in transaction" (no snapshot held, vacuum unaffected).
- Key spaces: the two-int form (scheduler, reaper, migrations) and the one-bigint form (domains) are separate lock spaces in Postgres, so they can't collide.

**Alternatives considered.** A leader election (a single scheduler process) or Redis locks with TTLs: more moving parts, and TTL locks can expire while the holder still works. A `domain_locks` table with expiry timestamps: the same problem.

**Consequences.** Locks vanish with their connection, which is the property we want for crash safety. The domain lock costs one pooled connection per running capture, so the worker pool is sized at `2 × concurrency + 4`. `hashtextextended` can collide for two domains, which would only mean an occasional needless 20 s wait.
