# Dockerized Cloud Development Environment

> Applies to the `testpapers-cloud` Compose stack owned by `TestPaper-backend`.
> Last updated: 2026-08-07 (CLE-80)

The Compose stack starts the complete cloud development environment —
Web frontend, Backend API, and PostgreSQL — with a single command. The API
applies pending Alembic migrations before starting the server. Redis and Celery
worker stay optional and are enabled by profile, so the core stack never
hard-depends on them. Host ports are bound to `127.0.0.1` only.

## Prerequisites

- Docker Engine with Compose v2 (`docker compose version`).
- A sibling checkout of the web repository at `../TestPapers` relative to this
  repository (see [WEB_CONTEXT](#configuration)), with its own `Dockerfile`.
- Nothing else is required to run the stack. No Node.js, Python, PostgreSQL, or
  Redis is needed on the host.

## Quick Start

```bash
# 1. Prepare environment variables (once)
cp .env.example .env

# 2. Build images (first start; rebuilt after dependency changes)
docker compose build

# 3. Start the core stack (postgres + api + web)
docker compose up -d

# 4. Check service status and logs
docker compose ps
docker compose logs -f --tail=100 api web
```

| Service | URL | Notes |
| --- | --- | --- |
| Web | <http://127.0.0.1:3000> | Nuxt dev server with hot reload |
| API | <http://127.0.0.1:8000> | FastAPI; OpenAPI at `/docs` |
| PostgreSQL | `127.0.0.1:5432` | credentials from `.env` |

## Developer Workflows

### 1. First-time environment setup

```bash
cp .env.example .env
# Optional: change ports/credentials, then re-run builds if images already exist.
```

### 2. Build all required images

```bash
docker compose build
```

### 3. Start the core stack

```bash
docker compose up -d
# Wait until api and web report healthy:
docker compose ps
```

The API waits for PostgreSQL, applies pending migrations, and then starts the
server. The `web` service waits for the API health check.

### 4. View status and logs

```bash
docker compose ps            # health columns
docker compose logs -f web   # follow one service
docker compose logs --tail=200 api
```

### 5. Manage database migrations

Pending migrations run automatically whenever the core stack starts. To apply
them explicitly after creating a new revision:

```bash
docker compose run --rm api alembic upgrade head
```

`api` mounts the host `alembic/` directory read-only. Generate new revision
files from the backend checkout, then rerun the migration command:

```bash
uv run --locked alembic revision --autogenerate -m "describe the change"
```

```bash
# Roll back one revision (dev only)
docker compose exec api alembic downgrade -1

# Validate upgrade/downgrade structure without a running database
docker compose exec api python scripts/simulate_migrations.py

# Initialize development data: create the first admin (non-interactive)
docker compose exec -e TESTPAPER_ADMIN_USERNAME=admin \
  -e TESTPAPER_ADMIN_PASSWORD='ChangeMe123!' \
  api python scripts/bootstrap_admin.py
```

### 6. Run Web / Backend verification

```bash
# Backend tests (from the backend checkout)
uv run --locked pytest -q

# Backend config preflight (from the backend checkout)
uv run --locked testpaper-config --env-file config/env/development.env.example

# Web checks (inside the web image; runs lint/typecheck/checks/build)
docker compose exec web npm run verify
```

The host-side backend commands require Python 3.13 and `uv`; they are optional
and are not needed to run the Compose stack.

### 7. Enable the Redis profile (optional)

```bash
docker compose --profile async up -d redis worker
# Or start the full async stack from scratch:
docker compose --profile async up -d
```

The API and core web flow run fine without Redis (rate limiting falls back to
an in-process counter, metadata caching falls back to the database). Redis is
required only for Celery tasks and realtime cross-instance broadcasts.

### 8. Stop services but keep data

```bash
docker compose stop
```

Named volumes (`postgres-data`, `runtime-data`, ...) persist across restarts.
Restarting the stack never deletes your development data.

### 9. Clean up containers and (after confirmation) development volumes

```bash
docker compose down          # remove containers + default network, keep volumes
docker compose down -v       # ALSO delete data volumes — confirm before running
docker compose --profile async --profile object-storage down -v   # incl. optional services
```

### 10. Troubleshooting

| Symptom | Likely cause / fix |
| --- | --- |
| `web` never becomes healthy | First Nuxt dev build takes a while; inspect `docker compose logs web`. |
| API does not start | Inspect `docker compose logs api`; migration failures or incorrect `POSTGRES_*` credentials prevent API startup. |
| Port already in use (3000/8000/5432) | Set `WEB_PORT`, `API_PORT`, `POSTGRES_PORT` in `.env` and re-run `docker compose up -d`. |
| Web can't reach the API | The browser uses same-origin `/api/v1`, proxied server-side to `http://api:8000/api/v1`. Confirm the API is healthy and inspect `docker compose logs web api`. |
| File permission errors on Linux | Host user id may differ from container user. For the web container (user `node`) ensure the checkout is readable; for the api container (user `testpapers`) the mounted source must be world-readable. |
| `WEB_CONTEXT` path not found | The web repo must be checked out as a sibling directory (`../TestPapers`) or set `WEB_CONTEXT` to its absolute path in `.env`. |
| Backend hot reload not picking up changes | Source mounts are read-only and uvicorn reloads on change; if a new directory is added, `docker compose up -d api` to recreate. |
| Stale dependency cache | After `pyproject.toml`/`uv.lock` or `package.json`/`package-lock.json` change: `docker compose build --no-cache api web`. |

## Configuration

All variables live in `.env` (see `.env.example`). Container-internal settings
are fixed by `compose.yaml`; host-facing values are overridable:

| Variable | Default | Purpose |
| --- | --- | --- |
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | `testpapers` / `testpapers` / `testpapers-local-only` | Local-only credentials for the dev database |
| `POSTGRES_PORT` / `API_PORT` / `WEB_PORT` | `5432` / `8000` / `3000` | Host ports (bound to 127.0.0.1) |
| `REDIS_PORT` / `MINIO_API_PORT` / `MINIO_CONSOLE_PORT` | `6379` / `9000` / `9001` | Optional profile host ports |
| `WEB_CONTEXT` | `../TestPapers` | Path to the web checkout used to build the `web` service |

Secrets (real passwords, production credentials) must never be committed. The
`.env` file is git-ignored; only `.env.example` is tracked.

## Environment Variable Boundaries

Three URL views exist and must not be confused:

- **Container-internal API URL**: `http://api:8000/api/v1` (server-to-server).
- **Host API URL**: `http://127.0.0.1:8000` (direct browser access while
  debugging the backend).
- **Browser public API URL**: `/api/v1` (same-origin, proxied by Nuxt to the
  container-internal URL).

The web container resolves these from `NUXT_API_BASE` (SSR) and
`NUXT_PUBLIC_API_BASE` (browser). Do not reuse the container-internal URL as a
browser-facing URL and vice versa.

## Production / Release Build

The `runtime` Dockerfile target produces the same images that staging and
production will use, sharing the `deps` and `build` stages with development:

```bash
# From the web repository root
docker build --target runtime \
  --build-arg NUXT_API_BASE=http://api:8000/api/v1 \
  --build-arg NUXT_PUBLIC_API_BASE=/api/v1 \
  -t testpapers-web:local ../TestPapers   # or . from the web checkout

# From the backend repository root
docker build -t testpaper-api:local .
```

The release image bakes the API endpoints at build time (Nitro proxy route
rules are static). The API endpoint must therefore be passed as a build-arg, not
only as a runtime environment variable. The Compose `web` service deliberately
uses the development target and bind mounts; run the immutable runtime image
standalone or from a production-specific Compose file.
