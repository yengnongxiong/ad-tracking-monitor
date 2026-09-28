# PRD: tag-monitor — Ad Tracking & Landing Page Monitor
Owner: Yengnong Xiong · Status: v1 · Type: Portfolio project (PM + SWE)

## 1. Summary
tag-monitor loads a business's ad landing pages in a real headless browser on a schedule and checks four things:
- whether the Meta Pixel and Google tags actually fire
- whether the page is fast on mobile
- whether the page renders correctly on mobile
- whether the page matches the ad that sends traffic to it

When something that used to work breaks, it sends one clear alert (not fifty), followed by a recovery notice when it's fixed. A research mode scans a list of public small business sites and publishes aggregate findings. An LLM check is validated against a human-labeled eval set.

Reliability, clear design decisions, and honest measured results matter more than feature count.

## 2. Problem
Businesses running Meta and Google ads depend on tracking tags to measure conversions, and the ad platforms use those signals to optimize delivery. Tags break silently: site redesigns, theme or plugin updates, removed tag manager containers, consent banners, and JavaScript errors all cause it. Ad spend keeps flowing while reporting and optimization degrade.

Slow or broken mobile landing pages waste paid clicks the same way.

Enterprise tag-auditing tools exist, but they are priced and built for large companies. Small businesses have nothing simple that watches their pages and tells them in plain English when something breaks.

## 3. Goals and non-goals
Goals:
- G1: Detect whether Meta Pixel, GA4, Google Ads, and GTM are installed AND firing, by observing real network requests rather than searching HTML.
- G2: Detect regressions and alert once. Use confirmation re-checks to avoid flaky false alarms; send reminders and recovery notices.
- G3: Measure mobile LCP (lab measurement) and catch mobile rendering problems.
- G4: Reliable job processing: a Postgres-backed queue with retries and backoff, crash recovery, horizontal scaling, and per-domain politeness.
- G5: Safe by design: SSRF protection for user-submitted URLs, and tenant isolation.
- G6: A research scan with published aggregate findings and an honest methodology section.
- G7: An LLM message-match check with measured agreement against human labels on a held-out test set.

Non-goals:
- fixing tags for users
- verifying server-side Conversions API events (not observable from a browser; document this)
- multi-step conversion flows (stretch goal)
- teams, billing, and SSO
- EU consent-mode edge cases (document as a limitation)

## 4. Users
- A small business owner spending roughly $500–$5,000/month on ads, non-technical. Needs "what's wrong, why it matters, and what to do" in plain English.
- A freelancer managing several clients' sites. Needs many sites per account and a quick status overview.

## 5. Success criteria for this project
- Detection is correct on every fixture site (§14) in CI.
- No job is processed twice under concurrent workers (tested), and a throughput benchmark with 1, 2, and 4 workers is published.
- Exactly one alert per incident in the flaky and persistent failure test scenarios.
- The SSRF test suite passes.
- The findings report is generated from a real scan.
- The eval report shows test-set agreement from real human labels.

## 6. Architecture
Components:
- web (Next.js): the UI. It rewrites /api/* to FastAPI so auth cookies are first-party.
- api (FastAPI): auth, sites, runs, alerts, ops.
- worker (Python, asyncio + Playwright Chromium): claims jobs, captures pages, runs checks, and updates alert state. Scale by running more worker containers.
- scheduler: a loop inside the worker process, guarded by an advisory lock so only one scheduler is active at a time.
- PostgreSQL 16: application data and the job queue.
- Object storage: MinIO locally (S3 API via boto3), an S3-compatible bucket in prod. Stores screenshots and capture JSON.
- Email: Mailpit locally (SMTP); Resend or SMTP in prod.
- Anthropic API: the message-match check.

Key design: capture once, analyze many.
- The browser step produces a serializable PageCapture: network requests, timings, console errors, DOM facts, extracted text, and screenshots.
- Every check is a pure analyzer over a PageCapture.
- This separates slow, flaky browser I/O from logic, makes checks unit-testable with saved JSON captures, and lets stored captures be re-analyzed without re-crawling.

The README must include a Mermaid architecture diagram.

## 7. Repo layout
```
tag-monitor/
  server/                     Python project (uv)
    src/tagmonitor/
      api/                    FastAPI routers, schemas, auth
      browser/                PageCapturer, network capture, SSRF guard, device profiles
      checks/                 base.py (Check ABC), one module per check, tracking_patterns.py
      queue/                  claim / complete / fail / heartbeat / reaper
      scheduler/
      alerts/                 state machine, outbox, EmailSender implementations, templates
      llm/                    message-match client, caching, usage logging
      evals/                  collect / label / split / run CLIs + metrics
      scan/                   research scan + analysis
      storage.py              object storage wrapper
      db/                     pool, migration runner, queries
      config.py
    tests/
      fixtures/sites/         local HTML fixture sites
      fixtures/captures/      saved PageCapture JSON
  db/migrations/
  web/
  evals/message_match/        prompts/, dataset/ (human-labeled), results/
  data/scan/                  targets.csv (provided by me)
  docs/
  docker-compose.yml
  Makefile
  .github/workflows/ci.yml
```

## 8. Tech stack (ask before adding anything else)
- Python 3.12+ with: uv, FastAPI, Pydantic v2, pydantic-settings, psycopg 3 (async) + psycopg_pool, Playwright for Python (Chromium), argon2-cffi, boto3, the anthropic SDK, httpx, Typer + Rich (CLIs), pandas + matplotlib (scan analysis), pytest + pytest-asyncio, ruff, mypy.
  - tldextract: use the bundled offline suffix list, with no network fetch at runtime.
  - scikit-learn: test-only, for cross-checking kappa.
- Web: Next.js (App Router), TypeScript, Tailwind CSS, Recharts.
- Infrastructure: PostgreSQL 16, MinIO, Mailpit, Docker Compose, GitHub Actions. The worker image is the official Playwright Python image.
- Use current stable versions; look them up rather than guessing.

## 9. Page capture (browser/)
Browser lifecycle:
- One Chromium per worker process. Relaunch it after 200 captures or on a crash.
- A new browser context per capture, for clean cookies and cache.

Devices:
- mobile: the Playwright "Pixel 7" descriptor
- desktop: 1440×900
- Scheduled monitoring captures both devices; scan mode captures mobile only.
- Optional mobile throttling via CDP (network and CPU): configurable, with the values documented. Off in tests.

Capture procedure:
1. Before navigation, inject a script with add_init_script that records LCP (PerformanceObserver, buffered) and layout shift.
2. Navigate with a 30 s timeout, waiting until "load".
3. Wait for network quiet: no new requests for 2 s, up to a maximum of 10 s. Tags often fire late.

Record:
- Every request: url, method, resource type, status, failure text, timing. Keep POST bodies only for tracking endpoints (§10).
- Console errors and uncaught page errors (top 10).
- DOM facts:
  - title, meta description, viewport meta, h1/h2 text
  - visible above-the-fold text (≤ 1,500 characters) and button/link texts (≤ 30)
  - scrollWidth vs innerWidth
  - final URL, redirect chain, and main document status
- A viewport-only JPEG screenshot (quality ~70) per device.

Limits and output:
- Hard timeout of 60 s per capture. Always close the context in a finally block.
- Output is a PageCapture: a Pydantic model, JSON-serializable, with a capture_schema_version.

SSRF guard. Applies to every navigation, every redirect hop, and every subresource request (via context.route):
- Only http and https; no credentials in the URL; only ports 80 and 443 for user-submitted URLs.
- Resolve hostnames and block if any resolved IP is:
  - private, loopback, or link-local (including 169.254.169.254)
  - multicast, reserved, or unspecified
  - in the CGNAT range 100.64.0.0/10
  - IPv6 ULA or link-local
- Handle IP literals written in decimal, hex, or octal. Cache resolutions per capture.
- Document the remaining DNS-rebinding risk and the defense-in-depth recommendation: run workers on a network with no access to internal services.
- Tests cover all of the above, including a public URL that redirects to 127.0.0.1.

## 10. Checks (checks/)
Base class: `class Check(ABC)` with `check_key: ClassVar[str]` and `analyze(capture: PageCapture, site: SiteConfig) -> CheckResult`.
- CheckResult has a status (pass | warn | fail | info | error), a plain-English summary, and a details dict.
- Adding a check means adding one class and registering it in one registry.

Tracking endpoint patterns:
- They live in tracking_patterns.py, with unit tests.
- These endpoints change over time. During M3, verify the patterns against real captures from a few live sites and record the date they were verified.

MetaPixelCheck:
- Script: requests to connect.facebook.net/*/fbevents.js (also /signals/config/<id>).
- Hits: requests to facebook.com/tr, GET or POST. Parse id and ev from the query string or the form body.
- Statuses:
  - firing (PageView seen) → pass
  - duplicate_pageview (PageView sent more than once for the same pixel id) → warn
  - installed_not_firing → fail
  - script_blocked (the script request failed) → fail
  - wrong_pixel (expected ids are configured and none were seen) → fail
  - not_installed → fail if expected ids are configured, otherwise info
- Details: pixel ids, and the events seen with their counts.

GoogleTagCheck:
- Endpoints to recognize:
  - GTM: googletagmanager.com/gtm.js?id=GTM-…
  - gtag.js: googletagmanager.com/gtag/js?id=G-…, AW-…, or GT-…
  - GA4 hits: *.google-analytics.com or analytics.google.com, path /g/collect. Parse tid and en, including from batched POST bodies.
  - Google Ads: googleads.g.doubleclick.net/pagead/viewthroughconversion/… and googleadservices.com/pagead/conversion/…
- Returns separate sub-results for GA4, Google Ads, and GTM, using the same status vocabulary as the Meta check. Expected ids are supported.

PageSpeedCheck (mobile):
- LCP ≤ 2.5 s → pass; ≤ 4 s → warn; > 4 s → fail. These are the Core Web Vitals thresholds; label the value as a lab measurement.
- Details: LCP, load time, transfer bytes, request count.

MobileRenderCheck:
- Missing viewport meta → fail.
- Horizontal overflow (scrollWidth > innerWidth + 2) → warn.
- Details include the screenshot keys.

PageHealthCheck:
- Main document status ≥ 400, or navigation failure → fail.
- Final URL on a different registrable domain → warn.
- More than 2 redirects → warn.
- Final URL not HTTPS → fail.
- JS errors → warn, listing the top errors.

MessageMatchCheck: added in M9; see §15.

Every status maps to a plain-English explanation and "how to fix" text, stored in one place (docs/check-explanations.md or a Python module) and reused by both the UI and the emails.

## 11. Data model (PostgreSQL)
Tables:
- users: id, email citext unique, password_hash, is_admin bool default false, created_at.
- sessions: id, user_id, token_hash unique, created_at, expires_at, last_seen_at.
- sites: id, user_id, name, url, normalized_url, registrable_domain, check_interval_minutes (60 | 360 | 1440, default 1440), next_check_at, paused, alert_email, expected_meta_pixel_ids text[], expected_ga4_ids text[], expected_google_ads_ids text[], ad_headline, ad_primary_text, ad_cta, created_at, updated_at. Unique (user_id, normalized_url).
- jobs: id bigserial, type, payload jsonb, status ('queued' | 'running' | 'succeeded' | 'failed' | 'dead'), priority int, run_at, attempts, max_attempts default 5, locked_by, locked_at, heartbeat_at, last_error, dedupe_key, created_at, finished_at.
  - Partial index on (priority DESC, run_at) WHERE status = 'queued'.
  - Partial unique index on dedupe_key WHERE status IN ('queued', 'running').
- check_runs: id, site_id (nullable), scan_id (nullable), job_id, device, started_at, finished_at, status ('completed' | 'error'), error_code, error_message, final_url, http_status, capture_key, screenshot_key, duration_ms.
- check_results: id, run_id, check_key, status, summary, details jsonb, created_at.
- site_check_states: site_id, check_key, state ('healthy' | 'suspect' | 'alerting'), consecutive_fails, state_entered_at, last_alerted_at, last_pass_at. PK (site_id, check_key).
- alerts: id, site_id, check_key, kind ('failure' | 'reminder' | 'recovery'), dedupe_key unique, subject, body_text, body_html, created_at, sent_at, attempts, last_error.
- scans: id, name unique, created_at, config jsonb.
- scan_targets: id, scan_id, url, category, source, status, skip_reason, run_id.
- llm_cache: cache_key PK, model, prompt_version, response jsonb, created_at.
- llm_usage: id, created_at, model, purpose, input_tokens, output_tokens.

Also:
- A view, site_latest_status, using DISTINCT ON (site_id, check_key), feeds the dashboard list.
- Comment every index with the query it serves.
- A retention job deletes runs, results, and captures older than 90 days (configurable), in batches.

## 12. Job queue, scheduler, workers
Claim jobs in a single statement:
```sql
UPDATE jobs SET status = 'running', locked_by = $1, locked_at = now(),
       heartbeat_at = now(), attempts = attempts + 1
WHERE id IN (
  SELECT id FROM jobs
  WHERE status = 'queued' AND run_at <= now()
  ORDER BY priority DESC, run_at
  FOR UPDATE SKIP LOCKED
  LIMIT $2)
RETURNING *;
```

Job types:
- capture_and_check {site_id, reason: scheduled | manual | confirm}
- send_alert {alert_id}
- scan_url {scan_target_id}
- retention

Priorities: manual 100, confirm 50, send_alert 40, scheduled 10, scan 0.

Outcomes:
- Success → succeeded.
- Failures are classified as transient (timeout, DNS failure, connection reset, 5xx, browser crash) or permanent (invalid URL, SSRF-blocked).
- Transient and attempts < max: back to queued, with run_at = now() + min(30 s × 2^(attempts−1), 30 min) ± 20% jitter.
- Otherwise → dead.
- Always store last_error.

Crash recovery:
- Workers send a heartbeat every 15 s while a job is running.
- A reaper runs every 60 s. It requeues running jobs whose heartbeat is more than 2 minutes old, or marks them dead if they are out of attempts.

Scheduler (every 30 s, under pg_try_advisory_lock). In one transaction:
1. Select due sites (next_check_at ≤ now(), not paused) FOR UPDATE SKIP LOCKED LIMIT 100.
2. Insert capture jobs with dedupe_key "site:<id>", ON CONFLICT DO NOTHING.
3. Set next_check_at = now() + interval ± 5% jitter.

Per-domain politeness:
- Before capturing, take pg_try_advisory_lock(hashtextextended(registrable_domain, 0)) on a connection held for the whole capture.
- If the lock is unavailable, set run_at = now() + 20 s without consuming an attempt.
- Scan mode also enforces at least 10 s between requests to the same domain.

Scaling and shutdown:
- WORKER_CONCURRENCY (default 3) capture slots per process, via asyncio.
- docker compose runs 2 worker containers to prove horizontal scaling.
- Graceful shutdown on SIGTERM: stop claiming new jobs, then finish or release in-flight jobs.

## 13. Alerts
Each (site, check_key) pair has a state machine.
- State transitions happen in the same transaction that saves the check results.
- Alert rows are written in that same transaction (transactional outbox pattern). A send_alert job then delivers them.

Transitions:
- healthy + fail → suspect; enqueue a confirm check in 10 minutes.
- suspect + fail → alerting; create a failure alert.
- suspect + pass → healthy; count it as a flake and send no alert.
- alerting + fail → stay in alerting; if last_alerted_at is more than 24 h ago, create a reminder alert.
- alerting + pass → healthy; create a recovery alert.
- warn results do not alert by default; they are visible in the dashboard.

Delivery:
- alerts.dedupe_key = site:check_key:kind:state_entered_at, so retries never double-send.
- sent_at is set only after the email provider accepts the message.
- An EmailSender ABC with three implementations: ConsoleEmailSender, SmtpEmailSender (Mailpit in dev), and ResendEmailSender.

Email content:
- A clear subject, for example "Your Meta Pixel stopped firing on example.com".
- What we saw, when it last worked (via a window function over check_results), why it matters, what to do, and a dashboard link.
- Plain text plus simple HTML.

Tests:
- Table-driven tests for every transition.
- Scenario tests:
  - flaky (fail, then pass) → 0 alerts
  - persistent (fail × 5 over 2 days) → 1 failure alert + 1 reminder
  - recovery → 1 recovery alert

## 14. Testing strategy
Isolation: tests never touch the real internet. All non-localhost requests are aborted, except the mocked tracking hosts.

Fixture sites live in tests/fixtures/sites and are served by a local server started in pytest:
- meta_ok, meta_not_firing, meta_duplicate_pageview, meta_wrong_id
- ga4_ok, gtm_ga4_ok, google_ads_ok, no_tags
- slow_lcp (the server delays the hero image by ~5 s), mobile_overflow, no_viewport
- http_500, redirect_chain_3, js_error, redirect_to_private_ip

Tracking stubs use Playwright routing:
- connect.facebook.net/** serves a stub fbevents.js. Its fbq('track', 'PageView') fires a request to https://www.facebook.com/tr?id=<id>&ev=PageView, which is fulfilled with a 1×1 GIF and recorded.
- Similar stubs for gtm.js and gtag.js send to GA4 /g/collect and the Google Ads endpoints.

Other tests:
- Analyzer unit tests use saved PageCapture JSON (fast, no browser).
- Queue: N concurrent claimers never claim the same job; backoff math; the reaper; the dedupe index.
- Auth: password hashing, sessions, the login rate limit, and IDOR (user A can't read or modify user B's sites or runs).
- The SSRF suite.
- Eval metrics are cross-checked against scikit-learn.

CI (GitHub Actions):
- Postgres service container; `playwright install --with-deps chromium`.
- ruff, mypy, pytest.
- web lint, typecheck, and build.

## 15. LLM message match + evals (M9)
The check:
- Runs only when the site has ad copy configured.
- Input: the ad headline, primary text, and CTA; the page title, meta description, h1, above-the-fold text, and button texts.
- Output (validated with Pydantic, with one retry on invalid output): offer_consistency, headline_relevance, cta_alignment, overall (each scored 1–5), issues[], suggestions[].
- overall ≤ 2 → fail; 3 → warn; ≥ 4 → pass.

Model settings:
- Model from the LLM_MODEL env var, default claude-haiku-4-5-20251001.
- Temperature 0, bounded max_tokens, a timeout, and retries on 429/5xx.
- Log token counts to llm_usage. Enforce an LLM_MAX_CALLS_PER_DAY guard.
- Cache key = sha256(model + prompt_version + ad copy + page text). Skip the call if nothing changed.
- Prompts are versioned files: evals/message_match/prompts/v1.md, v2.md, and so on.

Eval CLIs:
- collect: captures pages from a URL list and freezes their extracted text into dataset/pages.jsonl, so the dataset is reproducible.
- label: an interactive Rich CLI. It shows the ad and the page text and asks me for a 1–5 overall score and notes. It is resumable and writes dataset/labels.jsonl. The ad copy is written by me.
- split: a deterministic dev/test split (e.g., 70/30).
- run --prompt v2 --split dev|test: runs the model with a concurrency limit. It reports:
  - exact agreement and within-1 agreement
  - quadratic-weighted Cohen's kappa
  - MAE and a confusion matrix
  - per-example disagreements and token cost
  It writes a results JSON file and a markdown report comparing prompt versions.

Process rules:
- Iterate prompts on the dev split only, and report the final number on the test split once.
- Claude never creates, fills in, or modifies human labels.

## 16. Research scan (M8)
Input:
- data/scan/targets.csv with columns url, category, source. I compile it manually from public directories.
- Do not build scrapers for directory websites.

Command: `scan run --targets … --name fall-2026`.
- One mobile page load per domain.
- robots.txt is respected via urllib.robotparser. Disallowed sites are skipped and the skip is recorded.
- Priority 0, with the politeness limits from §12.
- Uses the same PageCapturer and checks as monitoring.

Command: `scan analyze --name fall-2026` writes docs/findings.md and docs/findings/*.png. It reports:
- sites attempted vs reachable
- % with the Meta script, and of those, % firing PageView
- % with duplicate PageView
- % with GA4 firing, % with Google Ads tags, % with GTM
- a mobile LCP histogram with the Core Web Vitals thresholds marked
- % with horizontal overflow, % missing viewport meta, % with HTTP errors
- a breakdown by category

Proportions are shown with 95% Wilson intervals.

The findings include a methodology and limitations section covering: sample source bias, single page loads, lab vs field measurement, consent banners, the scan date, and the user agent choice.

Only aggregate numbers are published, never per-site results or business names.

## 17. API
Auth:
- POST /api/auth/signup, /login, /logout; GET /api/auth/me.
- argon2id password hashing.
- 32-byte random session tokens, stored as SHA-256.
- Cookies are httpOnly and SameSite=Lax, and Secure in prod. Sessions last 30 days, sliding.
- Login rate limit: 5 attempts per 15 minutes per email+IP.
- Mutating requests require an X-Requested-With header (CSRF defense alongside SameSite).

Sites and runs:
- GET /api/sites (with the latest status per check).
- POST /api/sites: validate the URL, run the SSRF check, normalize it, and enqueue the first check immediately.
- GET/PATCH/DELETE /api/sites/{id}.
- POST /api/sites/{id}/check-now: at most once per 5 minutes per site; returns a job id.
- GET /api/jobs/{id}: job status, for polling.
- GET /api/sites/{id}/runs: keyset pagination on (started_at, id).
- GET /api/runs/{id}: results plus presigned screenshot URLs that expire after 10 minutes.
- GET /api/sites/{id}/trends?metric=lcp&days=30.
- GET /api/alerts.

Ops and health:
- GET /api/ops/queue (admin only): queue depth by status and type, the oldest queued job's age, success rate and p50/p95 job duration over 24 h, and dead jobs.
- GET /api/health.

Every query that touches sites, runs, or alerts is scoped by user_id.

## 18. Web (web/)
Pages:
- Public landing page: what it checks, why it matters, the research findings (numbers from docs/findings.md), and a sign-up CTA.
- Auth pages.
- Dashboard: a sites table with status dots for Meta, Google, Speed, Mobile, Health, and Message match; last checked (relative time); next check; a Check now button.
- Add/edit site: URL, name, interval, alert email, expected IDs (with help text on where to find them), and optional ad copy.
- Site detail:
  - status cards with a plain-English explanation and how-to-fix text
  - mobile and desktop screenshots
  - a run history grid (one colored cell per run per check)
  - an LCP trend chart
  - the tracking ids and events seen
  - job status polling every 3 s while a check runs
- Alerts page.
- Ops page (admin only).

Style: clean, responsive, and accessible. Tailwind; no heavy component library.

## 19. Documentation deliverables
README, containing:
- the pitch and a demo GIF placeholder
- the Mermaid diagram
- how detection works
- findings highlights
- the queue scaling benchmark
- eval results
- design decisions
- limitations
- the quickstart

docs/decisions.md, with ADRs covering at least:
- a Postgres queue vs Redis/Celery
- capture once, analyze many
- network observation vs HTML search
- the confirm re-check before alerting
- the transactional outbox
- advisory locks for the scheduler and for per-domain politeness
- the SSRF approach
- the same-origin proxy for cookies
- LLM caching and the dev/test split

Other docs: docs/findings.md, docs/performance.md, docs/check-explanations.md.

## 20. Milestones
Every milestone ends with tests passing, lint and type checks clean, and its acceptance criteria demonstrated.

- M0 Scaffold.
  - Build: repo layout, uv project, web skeleton, docker-compose (postgres, minio, mailpit, api, worker ×2, web), Makefile (dev, migrate, test, lint, check), .env.example, CI.
  - Accept: a fresh clone plus `make dev` works; CI is green.
- M1 Database schema and migration runner (§11).
  - Accept: migrations apply cleanly; constraint tests pass.
- M2 Page capture, SSRF guard, fixture sites, and tracking stubs (§9, §14).
  - Accept: each fixture produces the expected PageCapture; the SSRF suite passes; `python -m tagmonitor.capture <url>` works on a real site.
- M3 Checks (§10).
  - Accept: every fixture site yields the expected statuses; analyzers are unit-tested with saved captures; the tracking patterns are verified against a few live sites; `python -m tagmonitor.check <url>` prints a clear Rich report.
- M4 Queue, scheduler, workers, and storage (§12).
  - Accept: a concurrency test proves no job is claimed twice; retry/backoff and reaper tests pass; two workers process jobs from the same queue; screenshots and captures land in MinIO.
- M5 Alerts (§13).
  - Accept: all transition and scenario tests pass; emails are visible in Mailpit.
- M6 API and auth (§17).
  - Accept: auth, IDOR, rate-limit, and endpoint tests pass.
- M7 Web dashboard (§18).
  - Accept, all visible in the UI:
    1. sign up
    2. add a site
    3. the first check completes
    4. break a fixture's pixel
    5. the confirm check runs
    6. an alert arrives in Mailpit
    7. fix the pixel
    8. a recovery email arrives
- M8 Research scan (§16).
  - Accept: the scan runs on my targets.csv; findings.md and the charts are generated from the real run.
- M9 LLM message match and evals (§15).
  - Accept: the check works end to end; the eval CLIs work; the results report is generated from my labels.
- M10 Benchmarks, ops, docs, and deploy.
  - Build:
    - a queue throughput benchmark: 500 fixture captures with 1, 2, and 4 workers, reporting checks/min and verifying zero duplicates, charted in docs/performance.md
    - the ops page and the retention job
    - the README
    - deployment (I'll choose the provider)
  - Accept: all numbers come from real runs; the quickstart is verified from a fresh clone.

## 21. Stretch goals
- multi-step flows (verify Purchase/Lead events on a thank-you page via scripted steps)
- Slack and webhook alerts
- per-check alert preferences
- field data from the Chrome UX Report API
- A/B testing the onboarding flow with abtest-platform's SDK
