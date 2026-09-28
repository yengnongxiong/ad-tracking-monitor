# Kickoff review of the PRD

This is the output of the kickoff step: my read of the PRD, every gap or contradiction I found, how I resolved it, and the versions I'm building with. Decisions that shape the design also have a full entry in [decisions.md](decisions.md).

## 1. The project in brief

tag-monitor loads a small business's ad landing pages in a real headless Chromium on a schedule and tells the owner, in plain English, when their Meta Pixel or Google tags stop firing, or when the page is slow, broken on mobile, or doesn't match the ad. Detection is based on the network requests the page really makes, not on searching the HTML, so "installed but not firing" is caught. Each page load produces one serializable `PageCapture`, and every check is a pure function over it, so the browser is the only flaky part and every check is unit-testable from saved JSON. Work runs through a Postgres job queue (`FOR UPDATE SKIP LOCKED`) with retries, a reaper for crashed workers, per-domain politeness locks, and horizontally scalable workers. A per-check state machine with a confirmation re-check turns noisy results into exactly one alert per incident, then a reminder and a recovery notice, delivered through a transactional outbox. User-submitted URLs are treated as hostile: every connection the browser makes is checked by an SSRF guard. Two research features round it out: an aggregate scan of public small-business sites, and an LLM "message match" check measured against human labels.

## 2. Gaps, contradictions, and how I resolved them

| # | Issue | Resolution |
|---|---|---|
| 1 | **SSRF via `context.route` cannot see redirect hops.** I tested it: a route handler on Playwright 1.63 saw `/a` but not `/a → /b → /c`, even though the browser loaded all three. A public URL that redirects to `127.0.0.1` would bypass a route-based guard. | Enforce the guard in a **per-capture egress proxy** that the browser context is forced through (Playwright also forces loopback through it). Every connection is checked: navigations, every redirect hop, subresources, iframes, WebSockets and CORS preflights. The proxy connects to the IP it validated, which closes the DNS-rebinding gap for browser traffic. `context.route` is still used for tracking stubs. See ADR-004. |
| 2 | **MinIO's official image is gone from Docker Hub** (`minio/minio`: "repository does not exist"). The quay.io copy is frozen at a 2025 release. | Use `pgsty/minio`, a maintained community build of the same MinIO server, pinned to `RELEASE.2026-08-04T00-00-00Z` (amd64 + arm64). The app only talks S3 through boto3, so swapping it later is a one-line change. See ADR-001. |
| 3 | §12 lists "timeout, DNS failure, 5xx" as *transient job failures*, and §10 says the same things make PageHealthCheck *fail*. If a down site made the job retry until it's dead, no result would be saved and **a down site would never alert**. | Site-level failures are **data**: the capture records the navigation error or status, PageHealthCheck fails, and the confirm re-check provides the flake protection. Job retries are for **our** failures (browser crash, capture budget exceeded, storage or DB errors). SSRF-blocked and invalid URLs are permanent. See ADR-006. |
| 4 | When the page doesn't load, the Meta, Google, speed and mobile checks would all report fail, which means five alerts for one outage. | Checks that need a loaded page return `error` ("not evaluated: the page didn't load"). The state machine ignores `error`, so an outage produces exactly one alert, from Page health. |
| 5 | Monitoring captures two devices, but alert state is one row per (site, check). | Each check declares its devices: tracking and health run on both, speed and mobile rendering on mobile only. The state machine and the dashboard use the **worst** status across a job's devices, so a pixel broken only on mobile still alerts. |
| 6 | GoogleTagCheck returns three sub-results, but results and alert state are keyed by a single `check_key`. | Three check keys (`google_ga4`, `google_ads`, `google_gtm`) implemented in one module over a shared request parser. Alerts can say *which* tag broke. The dashboard's single "Google" dot shows the worst of the three. |
| 7 | `alerts.dedupe_key = site:check:kind:state_entered_at` means every reminder in one incident has the **same key**, so only the first reminder can ever be inserted. | Failure and recovery keys use `state_entered_at`. Reminder keys use the timestamp of the alert they follow (`last_alerted_at`), so each reminder is unique and a retried transition is still idempotent. |
| 8 | The confirm job uses the dedupe key `site:<id>`, but the job that enqueues it still holds that key (status `running`), so the insert would be silently skipped. | In the same transaction that saves the results, mark the current job `succeeded` **before** inserting the confirm job. |
| 9 | "Check now" while a scheduled or confirm job is already queued collides with the dedupe index. | Upsert: raise the queued job to manual priority and `run_at = now()`, and return its id. If a job is already running, return that id. |
| 10 | `not_installed` without expected IDs is `info`, so a pixel that disappears from a site with no expected IDs never alerts, which is the headline scenario. | Kept the PRD rule. In M7 the site page shows the detected IDs with a one-click "Monitor these IDs" button. A possible change is listed under open questions below. |
| 11 | The login rate limit needs storage that isn't in §11. | A `login_attempts` table, added in M6 as a new migration. It survives restarts and works with more than one API instance. |
| 12 | Presigned MinIO URLs signed for `http://minio:9000` are unreachable from the browser. | Sign with a separate public endpoint setting (`S3_PUBLIC_ENDPOINT_URL`). Signing makes no network call. |
| 13 | The M7 demo serves fixture sites on the Docker network, which the SSRF guard (correctly) blocks, and fake pixel hits would go to the real Meta and Google. | Two dev-only settings, both off by default: `SSRF_ALLOW_HOSTS` (exact hostnames exempt from the private-IP check) and `TRACKING_STUBS` (the same stub routes the tests use). The demo never sends traffic to Meta or Google. |
| 14 | The demo would wait 10 minutes for the confirm check, and the 5-minute "Check now" cooldown blocks the demo's fix-then-recheck step. | `CONFIRM_DELAY_SECONDS` (default 600) and `CHECK_NOW_COOLDOWN_SECONDS` (default 300) are configurable. The defaults follow the PRD, and the demo `.env` can lower them. |
| 15 | Mobile throttling is "optional", with no default given. | Default **off** (bounded capture times, fewer timeouts). A `slow4g` profile with Lighthouse's DevTools-throttling values is available, and LCP is always labeled "lab, unthrottled" or "lab, slow 4G". This is also under open questions. |
| 16 | IDs: §11 only specifies `bigserial` for jobs. | UUIDs for `users`, `sessions`, `sites` and `scans` (they appear in URLs and are not enumerable). `bigint` identity for high-volume internal rows (runs, results, alerts, targets, usage). See ADR-007. |
| 17 | Ports: the abtest-platform stack most likely uses 5432, 8000 and 3000. | Host ports default to 5433 (Postgres), 8001 (API) and 3001 (web), and are overridable in `.env`, so both stacks can run at once. |
| 18 | "Current stable" TypeScript is 7.0, the new Go-native compiler. Next.js's build-time type check uses the TypeScript 5 JavaScript API. | TypeScript 5.9.3, the version `create-next-app` pins. |
| 19 | Acceptance steps that need the public internet (M2 "works on a real site", M3 "patterns verified against live sites") can't run in the build sandbox, whose egress policy blocks general websites. | I built the commands and the offline tests. The live runs have to happen on your machine, and I won't write a "verified on" date until they're really run. |
| 20 | Scan mode's "≥ 10 s between requests to the same domain" rule. | Enforced structurally: one target per registrable domain, retry backoff of at least 30 s, plus the per-domain lock (M8). |

## 3. Versions (looked up on 2026-09-28)

| Component | Version | Source |
|---|---|---|
| Python | 3.12 (3.12.3 in the worker image) | `mcr.microsoft.com/playwright/python:v1.63.0-noble` |
| uv | 0.12.19 | PyPI |
| FastAPI / uvicorn | 0.141.1 / 0.54.0 | PyPI |
| Pydantic / pydantic-settings | 2.13.5 / 2.15.0 | PyPI |
| psycopg / psycopg-pool | 3.3.6 / 3.3.3 | PyPI |
| Playwright (Python) | 1.63.0, pinned to the same tag as the worker image | PyPI, MCR |
| argon2-cffi | 25.1.0 | PyPI |
| boto3 | 1.43.104 | PyPI |
| anthropic | 1.9.0 (added in M9) | PyPI |
| httpx | 0.28.1 | PyPI |
| Typer / Rich | 0.27.2 / 15.0.0 | PyPI |
| pandas / matplotlib | 3.0.6 / 3.11.2 (added in M8) | PyPI |
| pytest / pytest-asyncio | 9.1.1 / 1.4.0 | PyPI |
| ruff / mypy | 0.16.9 / 2.3.1 | PyPI |
| tldextract | 5.3.2 | PyPI |
| scikit-learn (test only) | 1.9.1 (added in M9) | PyPI |
| Node.js | 24 LTS ("Krypton") | nodejs.org |
| Next.js / React | 16.3.6 / 19.3.0 | npm |
| TypeScript | 5.9.3 (see issue 18) | npm |
| Tailwind CSS | 4.3.3 | npm |
| Recharts | 3.10.1 (added in M7) | npm |
| PostgreSQL | 16 (`postgres:16-alpine`) | Docker Hub |
| MinIO | `pgsty/minio:RELEASE.2026-08-04T00-00-00Z` | Docker Hub (see issue 2) |
| Mailpit | `axllent/mailpit:v1.x` | Docker Hub |

Dependencies are added in the milestone that first needs them, not all up front.

## 4. Open questions for you

None of these block the build. I picked a default for each so work could continue.

1. **Auto-baseline tags?** (issue 10) Today, a pixel that silently disappears only alerts if you entered expected IDs. Option: on the first successful check, save the detected IDs as the expected IDs automatically, with a notice so the user can edit them. It would change the add-site UX and the PRD's `not_installed` rule, so I left it for you to decide.
2. **Throttling default** (issue 15): unthrottled (current default: fast and stable, but it understates LCP) or `slow4g` (closer to real phones, slower and flakier)?
3. **Hosting** for M10, when you get there.
