# One image for both the API and the worker (docs/decisions.md, ADR-002).
# It starts from the official Playwright image because the worker needs Chromium and its
# system libraries; the Playwright Python package in uv.lock is pinned to the same version.
# Build context is the repo root so the image can include db/migrations.

FROM mcr.microsoft.com/playwright/python:v1.63.0-noble AS runtime
COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1
# Mirror the repo layout (/app/server, /app/db) so paths resolve the same way as on a laptop.
WORKDIR /app/server
# Install dependencies first so code edits don't invalidate this cached layer.
COPY server/pyproject.toml server/uv.lock server/.python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY server/src ./src
COPY db /app/db
# The worker reads the message-match prompt from here (tagmonitor/llm/prompts.py).
COPY evals/message_match/prompts /app/evals/message_match/prompts
RUN uv sync --frozen --no-dev
USER pwuser
CMD ["uvicorn", "tagmonitor.api.app:app", "--host", "0.0.0.0", "--port", "8000"]

# The dev image adds pytest, ruff and mypy. docker compose mounts the host source into it
# and runs as root so tools can write caches next to the mounted files.
FROM runtime AS dev
USER root
RUN uv sync --frozen
