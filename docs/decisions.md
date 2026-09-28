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
