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

**Consequences.** Every API request takes one extra hop through the Next.js server. FastAPI sees the Next.js server as the client. Verified in M6 against the Next.js 16.3 source: rewrites to an external URL are proxied **without adding `X-Forwarded-For`** (`httpxy` without `xfwd`), and a client-supplied `X-Forwarded-For` passes straight through. So the API must **not** trust that header behind this proxy (`TRUST_PROXY_HEADERS` defaults to false): if it did, an attacker could send a new fake IP with every login attempt and bypass the per-email+IP rate limit. Behind the Next.js proxy the login limit is therefore effectively per email, which still stops password guessing. In production, put a load balancer or CDN in front that *overwrites* `X-Forwarded-For`, and only then set `TRUST_PROXY_HEADERS=true` (the API uses the last hop). In production builds (`next build`), the rewrite destination is fixed at build time, so `API_INTERNAL_URL` must be set when building.

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

---

## ADR-012: Confirm a failure with a re-check before alerting (M5)

**Context.** Single page loads are noisy: a slow third-party script, a CDN hiccup or a deploy in progress can make a working pixel look broken once. An alert that cries wolf teaches owners to ignore alerts.

**Decision.** Each (site, check) has a state machine (`alerts/state_machine.py`, pure and table-tested): `healthy → suspect` on the first failure, which enqueues a **confirmation capture 10 minutes later** (`CONFIRM_DELAY_SECONDS`); a second failure makes it `alerting` and sends one email; a pass while suspect is logged as a flake with no email. While alerting, repeated failures stay silent except a reminder once 24 h have passed since the last email; a pass sends one recovery email. Warnings never alert. `error` results ("couldn't evaluate") change nothing. One confirmation job per site covers every check that just turned suspect. The site's `site:<id>` dedupe key keeps it from piling up with scheduled checks.

**Alternatives considered.**
- Alert on the first failure: fastest, but flaky.
- Require N failures in a row at the normal interval: with daily checks, that means days of delay.
- Retry inside the same job: a few seconds later is often the same transient problem.

**Consequences.** A real breakage is reported about 10 minutes after we first see it. Flakes cost one extra page load and no email. The PRD test scenarios hold (tested against the database): flaky → 0 alerts; five failures over two days → 1 failure + 1 reminder; recovery → 1 recovery.

---

## ADR-013: Transactional outbox for alerts (M5)

**Context.** Saving results, changing alert state and sending an email are three steps that can fail independently. Sending inside the results transaction risks emailing about results that then roll back. Sending after commit risks a crash between commit and send, which loses the alert.

**Decision.** In the **same transaction** that saves a job's results (and marks the job succeeded), `alerts/outbox.py` updates the state rows, inserts the alert with the email already rendered, and enqueues a `send_alert` job. A separate job delivers it and sets `sent_at` only after the provider accepts the message. Idempotency:
- `alerts.dedupe_key` is unique: `site:check:kind:<state_entered_at>` for failures and recoveries. Reminders use the time of the alert they follow, since every reminder in one incident shares `state_entered_at` (the PRD's key would allow only one reminder ever per incident).
- The send job skips alerts already marked sent.
- Resend gets the dedupe key as its `Idempotency-Key`; SMTP gets it as the `Message-ID`.

**Alternatives considered.** Sending from the capture job directly (loses or duplicates alerts as described above). A separate message broker (a second system for what one table and the existing queue already do).

**Consequences.** An alert is committed if and only if its results are, and it's delivered at least once; the provider-side idempotency key makes a duplicate unlikely even if a retry re-sends after a crash. Email content is frozen at alert time, so later edits to explanations don't rewrite history. The "last worked / failing since" lines come from a window-function query (`lag()` to find where the current failing streak began) over the check history.

---

## ADR-014: Server-side sessions in Postgres, cookie + header CSRF defense (M6)

**Context.** The dashboard needs login. The PRD asks for httpOnly SameSite=Lax cookies with 30-day sliding sessions, argon2id passwords, a login rate limit, and an X-Requested-With header on mutating requests.

**Decision.**
- **Sessions are rows**, not JWTs. The cookie holds 32 random bytes; the database stores only their SHA-256, so a database leak doesn't hand out working sessions. Logout deletes the row (JWTs can't really be revoked). Sessions slide: an active session is extended to 30 days, at most once an hour, so browsing doesn't write on every request.
- **Passwords** use argon2id (argon2-cffi defaults), hashed in a thread so the event loop keeps serving. Unknown emails still run a verify against a dummy hash, so response time doesn't reveal which emails have accounts, and both failures return the same message. Hashes are upgraded transparently if the default parameters get stronger.
- **CSRF:** SameSite=Lax already keeps the cookie off cross-site POSTs. On top of that, every mutating `/api/*` request must carry `X-Requested-With`, which a cross-site form can't add without a CORS preflight we never approve.
- **Login rate limit:** failed attempts are rows in `login_attempts` keyed by `email|ip`; the sixth failure within 15 minutes gets `429` with `Retry-After`, and a success clears the key. Stored in Postgres so it survives restarts and holds across API instances. The IP comes from the socket unless `TRUST_PROXY_HEADERS` is set (see ADR-003 for why it isn't behind the Next.js proxy).
- **Tenant isolation:** every query that touches sites, runs, jobs or alerts joins through `sites.user_id = current user`. Another user's id gets the same 404 as a missing one. A dedicated test walks every endpoint as the wrong user.

**Alternatives considered.** JWT access and refresh tokens (stateless, but revocation needs a denylist, i.e. state again, and tokens in JavaScript-readable storage are exposed to XSS). A full auth provider (Auth0, Clerk): the right call for a product, but it hides exactly the parts this project wants to show.

**Consequences.** One indexed lookup per request (`sessions.token_hash` is unique). Expired sessions and old login attempts need periodic cleanup (M10 retention).

---

## ADR-015: A local demo harness that can't weaken production (M7)

**Context.** The M7 acceptance flow (sign up → add a site → break a pixel → alert → fix → recovery) needs a landing page whose pixel can be broken on demand, inside Docker. That page sits on a private Docker address, which the SSRF guard (correctly) refuses, and its Meta and Google tags would send fake hits to the real vendors.

**Decision.**
- `demo/server.py` serves "Bean There Coffee" at `http://beanthere.demo/`, a Docker network alias (the `.demo` name exists only on the compose network) that is also published on `localhost:8088`. `make demo-break-pixel` / `make demo-fix-pixel` toggle a flag file, and the page then keeps loading the pixel but stops sending PageView.
- Only the dev compose file sets `SSRF_ALLOW_HOSTS=beanthere.demo` (an exact hostname) and `TRACKING_STUBS=true` on the API and workers. Stubs apply **only to pages on allowlisted hosts** (`PageCapturer.uses_tracking_stubs`), so a real site added to the dev stack still talks to the real tags.
- The dev `.env` shortens the confirmation delay (60 s instead of 600) and the "Check now" cooldown (20 s instead of 300). The code defaults stay at the PRD values.
- `scripts/demo_walkthrough.py` clicks through the whole flow in a real browser, saves screenshots, and can record a video for the README GIF.

**Alternatives considered.** Serving the demo page over HTTPS with a self-signed certificate and teaching Chromium to trust it (fragile and a lot of machinery for a demo); loosening the SSRF guard for all private addresses in dev (then dev no longer exercises the real guard).

**Consequences.** The demo is honest: the page really is served over plain HTTP, so Page health really does report "not HTTPS" and sends that alert too. Production is unaffected by construction: the allowlist is empty and stubs are off unless explicitly configured.

---

## ADR-016: The research scan reuses the monitoring pipeline, and publishes aggregates only (M8)

**Context.** PRD §16 asks for a one-off scan of a few hundred small-business landing pages, compiled by hand into `data/scan/targets.csv`, to produce findings like "X% have a Meta Pixel that never fires". The scan loads other people's websites, so it has to be polite and safe, and the published numbers must never identify a business.

**Decision.**
- **Same pipeline.** `scan run` vets the list, checks robots.txt, then enqueues one `scan_url` job per registrable domain at priority 0 (lowest), so the regular workers do the page loads with the same `PageCapturer`, SSRF guard, per-domain lock and checks as monitoring. Monitoring jobs always go first. Nothing about the scan needs its own worker, browser setup or retry logic.
- **robots.txt through the egress proxy.** The robots.txt fetch is also a request to a URL we were given, so it goes through a per-request `EgressProxy` (httpx with `proxy=`, `trust_env=False`), with the same DNS pinning and redirect checks as the browser. Rules follow RFC 9309: a 4xx means no rules, a 5xx means skip the site, and a network failure means the site is unreachable. The page load is scheduled at least 10 s after the robots.txt fetch (§12 politeness). Rules can target the `tag-monitor` token.
- **All or nothing.** Every robots.txt check runs before anything is written; the scan row, its targets and their jobs are then inserted in one transaction, so a crash can't leave a half-planned scan.
- **Clear denominators.** "Attempted" means robots.txt allowed us (or had no rules). Rows that aren't a domain name (typos, IP addresses) and duplicate domains are skipped as data-entry problems, not counted as unreachable businesses. Tag and layout shares use "loaded without an HTTP error" as the denominator; each row of the report states its own n.
- **Aggregates only.** The target list is git-ignored. The report contains counts, shares and 95% Wilson intervals; categories with fewer than 5 loaded sites are left out. A test asserts the generated report contains no URLs or hostnames. Only the capture JSON is stored (for re-analysis), not screenshots of third-party sites.

**Alternatives considered.** A standalone scanner script with its own browser loop (faster to write, but a second copy of the capture, SSRF and politeness logic that could drift from the one users rely on). Checking robots.txt inside each `scan_url` job (the plan and its skip counts would only be known after the whole scan ran, and the 10 s gap would have to be a sleep inside a job holding a worker slot). Normal-approximation intervals (they collapse to zero width at 0% and 100%, exactly the shares this report will have in small categories).

**Consequences.** The scan and monitoring share one worker pool, so a large scan slows nothing down but itself. Re-analysis is cheap: `scan analyze` reads rows already in Postgres, and the stored capture JSON allows re-running checks if a pattern changes. Sites behind a consent banner are measured as a first-time visitor sees them (no clicks), which the limitations section says.

---

## ADR-017: The model call happens outside the check; the check maps a verdict to a status (M9)

**Context.** Message match (PRD §15) needs a language model's judgment of the ad against the page text. Every other check is a pure function of `(capture, site)` (ADR-005): no network, no database, unit-testable with saved captures. An API call is slow, costs money, can fail, and needs a cache and a daily budget.

**Decision.**
- **Split the I/O from the judgment.** `tagmonitor.llm.MessageMatcher` does the I/O: the cache lookup, a call reserved against the daily budget, the request, and validation. The worker calls it once per job, on the mobile capture, after releasing the per-domain lock (the model call doesn't touch the site). `MessageMatchCheck.analyze(capture, outcome)` is pure: it maps the verdict (or why there isn't one) to pass, warn or fail. `run_checks` takes the outcome as an optional argument; without one (no ad copy) the check doesn't run. A shared `CheckBase` keeps titles, codes and explanations uniform across both kinds of check.
- **Structured output via a forced tool call.** The verdict's Pydantic model is the tool's input schema, and `tool_choice` forces the tool. The field order puts issues before scores, so the scores follow from what the model noticed. Invalid output gets exactly one retry, and the validation error goes back as an `is_error` tool result so the model can fix what was wrong.
- **Cache key:** sha256 of the model, the prompt version *and the prompt file's hash*, the ad copy, and the page text. Editing a prompt file in place can't serve stale answers. Pages rarely change, so most scheduled checks cost nothing.
- **Budget:** one `llm_usage` row is reserved per call, under a transaction-level advisory lock, before the call is made. Concurrent workers therefore can't overshoot `LLM_MAX_CALLS_PER_DAY` (a test runs 10 concurrent callers against a limit of 3). Every attempt counts, failed ones included, because the cap bounds what could be billed. Evals share the budget.
- **Failure is a result, not a job failure.** An API error, the daily limit, or invalid output becomes an "error" result, which never alerts and never costs the site its other checks. No API key gives an "info" result ("turned off").
- **Sampling.** The API has deprecated `temperature` (the current SDK no longer has the parameter). The default model, Haiku 4.5, still accepts it, so `LLM_TEMPERATURE=0` is sent in the request body, and it can be unset for newer models that reject it. Reproducibility doesn't depend on it: the cache returns the same verdict for the same inputs.
- **Evals reuse the production path.** `evals run` calls the same `MessageMatcher` (purpose `eval` in `llm_usage`), so what is measured is what ships. The split is by page, not by example, so no page's text is in both dev and test. Scoring test a second time needs `--rerun-test` and is flagged in the report.

**Alternatives considered.**
- Calling the API inside the check. That breaks purity, and a saved capture could no longer be re-analyzed offline.
- Storing the verdict inside the PageCapture. A capture records what the browser saw; the verdict also depends on the ad copy, which isn't a property of the page.
- Structured outputs (`output_config.format`). It's newer, and its JSON-schema support differs by model, while a forced tool call works on every current model.
- Counting the budget by summing `llm_usage` without a lock. Two workers could both read "499" and both call.

**Consequences.** The prompt text lives in `evals/message_match/prompts/`, outside the Python package, so the Docker image copies it in and compose mounts it. The check only sees text, not images, and the prompt tells the model so. The first real API call happens when you add your key: the request format is verified against the SDK with a mock HTTP server, not against the live API.

---

## ADR-018: Retention keeps each site's latest state and deletes rows before objects (M10)

**Context.** Every check stores two runs, about eight results, two capture JSONs and two screenshots. PRD §11 asks for a retention job that deletes runs, results and captures older than 90 days (configurable), in batches.

**Decision.**
- **Daily, via the scheduler.** Every scheduler tick checks whether a `retention` job ran in the last day (a tiny partial index makes the check cheap) and enqueues one if not; the `retention` dedupe key keeps it to one.
- **Batches of 500 runs,** each its own statement, so a large backlog never holds a long transaction or locks the table. 500 runs means at most 1,000 objects, which is S3's `DeleteObjects` limit.
- **Always keep each site's latest run per device.** The dashboard shows the latest results, so a site paused for four months still shows its last known state instead of going blank.
- **Rows first, then objects.** If an object delete fails, the result is an orphaned file, not a run pointing at a missing screenshot.
- **Also pruned:** old finished jobs, expired sessions, login attempts older than a day, and model answers cached before the cutoff.
- **Kept:** alerts (the user's record of what happened), LLM usage (cost history), and research-scan runs (deleting a scan removes them).

**Alternatives considered.**
- Postgres table partitioning by month, dropping old partitions. It's the right tool at much larger volumes. Here it would complicate foreign keys and the latest-run rule, for tables this size.
- Object-store lifecycle rules for captures. Simpler, but they can't know which captures are a site's latest, and they drift from the database.

**Consequences.** The run history grid and the LCP chart show at most `RETENTION_DAYS` of history. The "last worked" line in alert emails can only look back that far too.
