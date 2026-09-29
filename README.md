# tag-monitor

[![CI](https://github.com/yengnongxiong/ad-tracking-monitor/actions/workflows/ci.yml/badge.svg)](https://github.com/yengnongxiong/ad-tracking-monitor/actions/workflows/ci.yml)

**Know the moment your Meta Pixel or Google tags stop firing on your ad landing pages.**

Small businesses spend real money sending ad clicks to landing pages whose tracking silently broke: a theme update drops the pixel, a cookie banner blocks it, someone deletes the Tag Manager container. The ads keep spending, reporting goes blank, and the platforms' bidding optimizes for the wrong people. tag-monitor loads each landing page in a real headless browser on a schedule, watches the network requests the tags actually send, and emails once, in plain English, when something that used to work breaks, then again when it's fixed.

![Demo: add a page, break its pixel, get one alert, fix it, get the recovery email](docs/img/demo.gif)

<sub>The whole flow at 4x speed: sign up, add a page, break its Meta Pixel, the confirmation re-check, the alert email, the fix, and the recovery email. Recorded from the local demo with <code>scripts/demo_walkthrough.py</code>.</sub>

## What it checks

| Check | How | Fails when |
|---|---|---|
| **Meta Pixel** | `fbevents.js` loads, and `facebook.com/tr` hits carry `ev=PageView` (GET or POST) | installed but not firing, script blocked, wrong pixel ID, missing (if you said it should be there); PageView sent twice is a warning |
| **GA4 / Google Ads / Tag Manager** | `/g/collect` hits (including batched POST bodies), the Ads conversion endpoints, `gtm.js` and `gtag` loads | the same vocabulary as Meta, per tag |
| **Mobile speed** | Largest Contentful Paint on an emulated Pixel 7 (a lab measurement) | over 4 s (over 2.5 s warns) |
| **Mobile layout** | viewport meta tag, horizontal overflow at the device's width | missing viewport; overflow warns |
| **Page health** | the main document's status, redirects, final domain, HTTPS, JavaScript errors | an error page, plain HTTP; unusual redirects and JS errors warn |
| **Message match** (optional) | Claude compares your ad copy with the text a phone shows before scrolling | the page doesn't deliver what the ad promised |

Every outcome has a plain-English "what we saw / why it matters / how to fix it", shared by the dashboard and the emails: [docs/check-explanations.md](docs/check-explanations.md).

The tag patterns were checked against live sites on 2026-09-28: on four large online retailers' home pages, the Meta Pixel, GA4 and Google Ads traffic all matched, and two newer Google endpoints the run turned up were added ([build log](docs/build-log.md#verification-pass-2026-09-28)).

## How it works

```mermaid
flowchart LR
    user([Site owner]) -->|browser| web[Next.js dashboard]
    web -->|"/api/* rewrite, same origin"| api[FastAPI]
    api --> pg[(Postgres<br/>sites, results, alert states,<br/>job queue, LLM cache)]
    subgraph workers["Workers (N processes x 3 slots)"]
        sched[Scheduler tick<br/>advisory lock] --> pg
        w[Job loop<br/>FOR UPDATE SKIP LOCKED] --> pg
        w --> chromium[Headless Chromium<br/>Playwright]
        chromium --> proxy[SSRF egress proxy<br/>DNS pinning, public IPs only]
        w --> checks[Pure checks<br/>capture in, result out]
        w -->|message match only| claude[Anthropic API]
    end
    proxy --> site([Landing page + its tags])
    w --> s3[(MinIO / S3<br/>captures, screenshots)]
    w -->|send_alert jobs| mail[SMTP / Resend]
    pg --- w
```

**Capture once, analyze many.** A worker loads the page on mobile and desktop and records everything into a `PageCapture`: every network request (including POST bodies), the redirect chain, console errors, performance timings, and DOM facts. It stores the capture as JSON next to a screenshot. The checks are pure functions of that capture, so they're unit-tested against saved captures and can be re-run on old ones when a detection pattern changes ([ADR-005](docs/decisions.md#adr-005-capture-once-analyze-many-m2)).

**Network observation, not HTML search.** A pixel snippet in the HTML proves nothing; a `PageView` request leaving the browser does. The checks recognize the tags' real endpoints, which catches the failures that matter: installed but never fires, blocked, the wrong ID, firing twice ([ADR-008](docs/decisions.md#adr-008-detect-tags-by-observing-network-requests-not-by-searching-the-html-m3)).

**One clear alert, not fifty.** Each (site, check) pair has a small state machine (healthy, suspect, alerting). A first failure only makes the check "suspect" and schedules a confirmation re-check 10 minutes later; only a second failure alerts. A page that's down produces one "your page is down" alert, not one per tag. Alert rows are written in the same transaction as the results that caused them (a transactional outbox), and a dedupe key means a retry never sends twice ([ADR-012](docs/decisions.md#adr-012-confirm-a-failure-with-a-re-check-before-alerting-m5), [ADR-013](docs/decisions.md#adr-013-transactional-outbox-for-alerts-m5)).

**A Postgres job queue.**
- Workers claim jobs with a single `UPDATE ... WHERE id IN (SELECT ... FOR UPDATE SKIP LOCKED)`.
- Leases have heartbeats and fencing tokens, a reaper rescues jobs from dead workers, and retries use exponential backoff with jitter.
- An advisory lock lets one scheduler tick run at a time.
- A per-domain advisory lock loads at most one page per website at a time, across all workers ([ADR-010](docs/decisions.md#adr-010-the-job-queue-lives-in-postgres-m4), [ADR-011](docs/decisions.md#adr-011-advisory-locks-for-the-scheduler-the-reaper-and-per-domain-politeness-m4)).

**Loading a URL a user typed, safely.** Every request the browser makes (navigation, redirect hop, subresource) goes through a per-capture egress proxy. The proxy resolves DNS itself, refuses private, loopback, link-local and metadata addresses (including IPv4-mapped IPv6, 6to4, Teredo and NAT64 forms), and connects only to addresses it checked ([ADR-004](docs/decisions.md#adr-004-enforce-the-ssrf-guard-in-a-per-capture-egress-proxy-not-only-in-contextroute-m2)).

## Queue scaling benchmark

`make bench` runs 500 real site checks against local fixture pages with 1, 2, 4 and 8 worker processes and verifies that no job ran twice. Results, with the machine they ran on: [docs/performance.md](docs/performance.md).

## Research findings

The research scan loads a hand-compiled list of public small-business websites once each, on a phone profile, respecting robots.txt, and publishes aggregate numbers only. It uses the same capture and checks as monitoring ([ADR-016](docs/decisions.md#adr-016-the-research-scan-reuses-the-monitoring-pipeline-and-publishes-aggregates-only-m8)).

**Not run yet.** The tooling is built and tested; the target list is compiled by hand. `make scan NAME=...` then `make findings NAME=...` writes `docs/findings.md` (methodology, limitations, 95% Wilson intervals) and the landing page's numbers.

## Message match evals

How often does the model's 1-5 message-match score agree with a person's? The tooling ([evals/message_match/README.md](evals/message_match/README.md)) covers:

- **Collection** freezes each page's text, so the dataset stays reproducible.
- **Labeling** is a blind, resumable terminal session.
- **The split** is by page, so no page's text is in both dev and test.
- **Scoring** runs a prompt version through the same code path as production. The report gives exact and within-1 agreement, quadratic-weighted kappa (cross-checked against scikit-learn), MAE, bias, a confusion matrix, and cost.
- **The rule:** prompts are tuned on dev, and test is scored once.

**Not run yet.** The labels have to come from a person, and the results will be reported from `evals/message_match/results/report.md`.

## Design decisions

All in [docs/decisions.md](docs/decisions.md), each with context, the decision, the alternatives considered and the consequences. The ones worth reading first:

- **A Postgres queue instead of Redis/Celery.** The jobs, the results they produce and the alerts those trigger commit in one transaction. There's one fewer system to run, and `SKIP LOCKED` is fast enough at this scale (see the benchmark). [ADR-010](docs/decisions.md#adr-010-the-job-queue-lives-in-postgres-m4)
- **Capture once, analyze many.** [ADR-005](docs/decisions.md#adr-005-capture-once-analyze-many-m2)
- **Network observation vs HTML search.** [ADR-008](docs/decisions.md#adr-008-detect-tags-by-observing-network-requests-not-by-searching-the-html-m3)
- **A confirmation re-check before alerting,** and **a transactional outbox** for the emails. [ADR-012](docs/decisions.md#adr-012-confirm-a-failure-with-a-re-check-before-alerting-m5), [ADR-013](docs/decisions.md#adr-013-transactional-outbox-for-alerts-m5)
- **Advisory locks** for the scheduler and for per-domain politeness. [ADR-011](docs/decisions.md#adr-011-advisory-locks-for-the-scheduler-the-reaper-and-per-domain-politeness-m4)
- **The SSRF egress proxy,** because Playwright's request interception never sees redirect hops. [ADR-004](docs/decisions.md#adr-004-enforce-the-ssrf-guard-in-a-per-capture-egress-proxy-not-only-in-contextroute-m2)
- **A same-origin proxy** so the session cookie stays first-party. [ADR-003](docs/decisions.md#adr-003-same-origin-proxy-for-the-api-m0)
- **The LLM call outside the pure check,** with caching, a race-free daily budget, and the dev/test split. [ADR-017](docs/decisions.md#adr-017-the-model-call-happens-outside-the-check-the-check-maps-a-verdict-to-a-status-m9)

## Limitations

- **Consent banners aren't clicked.** Tags that wait for consent look "installed but not firing" to a first-time visitor in a region where the banner blocks them. That's what that visitor's browser does too, but it isn't always a bug.
- **Server-side tracking is invisible.** Meta's Conversions API and server-side Tag Manager on a first-party domain can't be seen from a browser.
- **Endpoint patterns drift.** The tags' request formats change over time. The patterns in `checks/tracking_patterns.py` are unit-tested and were last checked against live sites on 2026-09-28; `python -m tagmonitor.verify_patterns URL...` re-checks them and lists anything new.
- **Lab, not field, speed.** LCP comes from one emulated page load per check, not from real visitors' phones.
- **Message match reads text only.** Images and video aren't judged, and the verdict is a model's opinion, measured against human labels in the evals.
- **One region, one user agent.** Geo-targeted pages, and bot defenses that single out headless browsers, can show something different.
- **Not deployed yet.** Everything runs locally with Docker Compose. Hosting is still to be chosen.

## Quickstart

Requirements: Docker (with Compose v2) and `make`.

```bash
git clone https://github.com/yengnongxiong/ad-tracking-monitor.git && cd ad-tracking-monitor
make dev          # the first run copies .env.example to .env, then builds and starts everything
```

| Service | URL |
|---|---|
| Dashboard | http://localhost:3001 |
| API health | http://localhost:8001/api/health |
| Mailpit (captured emails) | http://localhost:8025 |
| MinIO console | http://localhost:9001 |

`make help` lists every target, including the scan, eval and benchmark ones. To make yourself an admin (for the Ops page), run `docker compose run --rm api python -m tagmonitor.admin make-admin you@example.com`. To check a real page from the command line: `make check URL=https://example.com`.

Message match needs `ANTHROPIC_API_KEY` in `.env`. Without it, the check reports that it's turned off.

### Try the demo

The dev stack includes a demo landing page, "Bean There Coffee", with a Meta Pixel and GA4. Its tags are answered by local stubs, so nothing is sent to Meta or Google.

1. Open http://localhost:3001, sign up, and add `http://beanthere.demo/` as a page.
2. Run `make demo-break-pixel`, then click **Check now**. After the confirmation re-check (60 s in dev), an alert lands in Mailpit (http://localhost:8025).
3. Run `make demo-fix-pixel`, then click **Check now** again for the recovery email.

`scripts/demo_walkthrough.py` does all of this in a browser and records the video the GIF above comes from.

## Development

| Command | What it does |
|---|---|
| `make test` | the Python suite in Docker, against real Postgres, MinIO and Mailpit (no internet: tag hosts are stubbed, DNS is a fixed table) |
| `make lint` | ruff, ruff format, strict mypy, eslint and tsc |
| `make fmt` | ruff format and autofix |
| `make migrate` | apply new files in `db/migrations/` (also runs automatically on `make dev`) |
| `cd web && npm ci && npm run dev` | the dashboard on the host with fast refresh, proxying `/api` to the API on port 8001; file watching doesn't reliably reach the web container |
| `cd server && uv sync && uv run pytest` | tests on the host (needs `docker compose up -d postgres minio mailpit`) |

CI runs the same lint, the full test suite with Chromium, and a production build of the dashboard on every push.

## Repository map

```
server/          Python: API, worker, checks, queue, alerts, scan, LLM, evals (uv project)
  benchmarks/    the queue throughput benchmark
web/             Next.js dashboard
db/migrations/   plain SQL, applied in order by the migrate service
evals/           message-match prompts, dataset, results
data/scan/       the research scan's target list (git-ignored)
docs/            PRD, design decisions, build log, check explanations, performance
demo/            the Bean There Coffee demo page
scripts/         the end-to-end demo walkthrough
```

Built in milestones from the product spec, [docs/PRD.md](docs/PRD.md). What each one added and how it was verified: [docs/build-log.md](docs/build-log.md).
