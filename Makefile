# Common tasks. Everything runs in Docker, so the only host requirements are Docker and make.
COMPOSE := docker compose

.PHONY: help dev down migrate test lint fmt check demo-break-pixel demo-fix-pixel scan findings

help: ## List the available targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  %-8s %s\n", $$1, $$2}'

.env:
	cp .env.example .env

dev: .env ## Start the stack: postgres, minio, mailpit, api, 2 workers, web
	$(COMPOSE) up --build

down: ## Stop the stack (data volumes are kept)
	$(COMPOSE) down

migrate: .env ## Apply pending SQL migrations in db/migrations
	$(COMPOSE) run --rm migrate

test: .env ## Run the Python tests against the compose Postgres, MinIO and Mailpit
	$(COMPOSE) up -d --wait postgres minio mailpit
	$(COMPOSE) run --rm --build tests

lint: .env ## ruff, mypy, eslint, tsc
	$(COMPOSE) run --rm --no-deps --build tests sh -c "ruff check . && ruff format --check . && mypy"
	$(COMPOSE) run --rm --no-deps --build web sh -c "npm run lint && npm run typecheck"

fmt: .env ## Auto-format and auto-fix Python code
	$(COMPOSE) run --rm --no-deps tests sh -c "ruff format . && ruff check --fix ."

check: .env ## Capture one URL on mobile and desktop and print a report: make check URL=https://...
	@test -n "$(URL)" || (echo "usage: make check URL=https://example.com" && exit 1)
	$(COMPOSE) run --rm --no-deps api python -m tagmonitor.check "$(URL)"

demo-break-pixel: ## Demo: the Bean There page keeps its Meta Pixel but stops sending PageView
	@mkdir -p demo/state && touch demo/state/pixel-broken && echo "pixel broken: click Check now in the dashboard"

demo-fix-pixel: ## Demo: the Bean There page sends PageView again
	@rm -f demo/state/pixel-broken && echo "pixel fixed: click Check now in the dashboard"

scan: .env ## Research scan of data/scan/targets.csv (needs `make dev` running): make scan NAME=fall-2026
	@test -n "$(NAME)" || (echo "usage: make scan NAME=fall-2026" && exit 1)
	$(COMPOSE) run --rm --no-deps -v ./data:/app/data api \
		python -m tagmonitor.scan run --targets /app/data/scan/targets.csv --name "$(NAME)" --wait

findings: .env ## Write docs/findings.md from a finished scan: make findings NAME=fall-2026
	@test -n "$(NAME)" || (echo "usage: make findings NAME=fall-2026" && exit 1)
	$(COMPOSE) run --rm --no-deps -v ./docs:/app/docs api \
		python -m tagmonitor.scan analyze --name "$(NAME)" --docs-dir /app/docs
