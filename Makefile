# Common tasks. Everything runs in Docker, so the only host requirements are Docker and make.
COMPOSE := docker compose

.PHONY: help dev down test lint fmt

help: ## List the available targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  %-8s %s\n", $$1, $$2}'

.env:
	cp .env.example .env

dev: .env ## Start the stack: postgres, minio, mailpit, api, 2 workers, web
	$(COMPOSE) up --build

down: ## Stop the stack (data volumes are kept)
	$(COMPOSE) down

test: .env ## Run the Python tests against the compose Postgres, MinIO and Mailpit
	$(COMPOSE) up -d --wait postgres minio mailpit
	$(COMPOSE) run --rm --build tests

lint: .env ## ruff, mypy, eslint, tsc
	$(COMPOSE) run --rm --no-deps --build tests sh -c "ruff check . && ruff format --check . && mypy"
	$(COMPOSE) run --rm --no-deps --build web sh -c "npm run lint && npm run typecheck"

fmt: .env ## Auto-format and auto-fix Python code
	$(COMPOSE) run --rm --no-deps tests sh -c "ruff format . && ruff check --fix ."
