# CLAUDE.md — tag-monitor

## What this is
An ad tracking and landing page monitor built as a portfolio project for PM and SWE internship applications. The full spec is docs/PRD.md. Read the relevant PRD sections before starting any task. The PRD is the source of truth: if something in it is wrong or impossible, stop and tell me. Never silently change the design.

## How we work
- One milestone at a time (see the Milestones section of the PRD). Never build features from later milestones.
- Plan before coding and wait for my approval on plans.
- Small, reviewable changes. Prefer simple, boring solutions over clever ones.
- Ask before adding any dependency not listed in the PRD's tech stack section.
- I must be able to understand and defend every line in interviews. Favor readable code, clear names, and short docstrings that explain WHY. No dead code, no speculative abstractions.
- Never invent numbers in docs or the README. Only write numbers produced by commands actually run in this repo, and note the command that produced them.
- Record significant design decisions in docs/decisions.md using: Context / Decision / Alternatives considered / Consequences.
- Don't commit or push unless I ask. Never force-push. Never commit secrets; use .env (gitignored) and keep .env.example up to date.

## Commands (defined in M0; keep this section updated)
- make dev: docker compose up (postgres, minio, mailpit, api, 2 workers, web)
- make test: pytest in Docker against the compose Postgres/MinIO/Mailpit
- make lint: ruff, ruff format --check, mypy, eslint, tsc
- make fmt: ruff format + ruff check --fix
- make down: stop the stack
- make migrate: apply pending db/migrations (also runs automatically as the `migrate` service in make dev)
- make check URL=https://example.com: capture one URL on mobile and desktop, run all checks, print a report
- python -m tagmonitor.verify_patterns URL...: re-verify tracking endpoint patterns on live sites
- make scan NAME=... / make findings NAME=...: research scan of data/scan/targets.csv and its report
- make eval-status / eval-collect / eval-label / eval-split / eval-run PROMPT=v1 SPLIT=dev / eval-report: message-match evals (evals/message_match/README.md)
- make bench: queue throughput benchmark (500 fixture checks, 1/2/4/8 worker processes) -> docs/performance.md
- python -m tagmonitor.checks.explanations > docs/check-explanations.md: regenerate the explanations doc
- python -m tests.save_fixture_captures (in the tests container): regenerate saved fixture captures
- Host-side alternative: `cd server && uv sync && uv run pytest` (needs `make dev` or `docker compose up -d postgres minio mailpit` running)

## Code standards
- Python 3.12+ with uv. Type hints everywhere; mypy strict on src/. ruff for lint and format. Pydantic v2.
- psycopg 3 (async) with parametrized SQL only; never build SQL with f-strings or string formatting that contains values.
- Checks are pure analyzers over a PageCapture: no browser, network, or DB access inside a check. Browser code lives only in browser/.
- TypeScript: strict mode, no `any`.
- SQL migrations are plain numbered .sql files in db/migrations/. Never edit an applied migration; add a new one. Comment every index with the query it serves.
- Tests: pytest + pytest-asyncio against the real Postgres and MinIO from docker compose. Every bug fix gets a regression test.

## Hard rules
- Tests must never contact the real internet. Only localhost fixture servers and mocked tracking hosts are allowed.
- Every navigation, redirect hop, and subresource request goes through the SSRF guard. Never add a code path that fetches a user-supplied URL without it.
- Never create, edit, or "fill in" human labels in evals/message_match/dataset/. You may build the tools; the labels are mine.
- Don't build scrapers for directory websites. Scan targets come only from data/scan/targets.csv, which I provide.
- Never publish per-site scan results or business names. Aggregates only.
