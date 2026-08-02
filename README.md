# TestPaper Backend

> Version: 0.1.0  
> Framework: FastAPI 0.136  
> Python: 3.13+  
> Last updated: 2026-08-02

FastAPI backend for the TestPapers test paper management and auto-generation system. The service exposes REST and WebSocket APIs, uses PostgreSQL for persistence including collaborative paper drafts, Redis for cache/Celery broker, and Celery for asynchronous jobs.

## Tech Stack

| Technology | Purpose |
| --- | --- |
| Python 3.13+ | Runtime |
| FastAPI | REST and WebSocket framework |
| SQLAlchemy 2.0 | ORM |
| PostgreSQL | Primary database |
| Alembic | Database migrations |
| Redis | Cache, Celery broker/result backend, session and rate-limit storage |
| Celery | Async task queue |
| Argon2-cffi | Password hashing |
| Uvicorn | ASGI server |

## Local Setup

```bash
uv sync
```

Alternative:

```bash
pip install -e .
```

## Local Commands

```bash
testpaper-backend
uvicorn testpaper_backend.application:app --reload
celery -A testpaper_backend.worker.celery_app:celery worker --loglevel=info
alembic upgrade head
python scripts/bootstrap_admin.py
ruff format .
ruff check .
pytest
python scripts/check.py
python scripts/export_openapi.py --check
```

## Environment Variables

Required:

```text
DATABASE_URL=postgresql+psycopg://postgres:password@localhost:5432/testpapers
```

Common optional values:

```text
API_HOST=0.0.0.0
API_PORT=8000
REDIS_URL=redis://localhost:6379/0
REDIS_MAX_CONNECTIONS=50
REDIS_SOCKET_TIMEOUT=5.0
REDIS_SOCKET_CONNECT_TIMEOUT=2.0
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0
CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
AUTH_COOKIE_NAME=testpapers_session
AUTH_COOKIE_SECURE=false
AUTH_COOKIE_SAMESITE=lax
AUTH_COOKIE_DOMAIN=
CSRF_COOKIE_NAME=testpapers_csrf
SESSION_TTL_HOURS=12
RATE_LIMIT_MAX_ATTEMPTS=5
RATE_LIMIT_WINDOW_SECONDS=60
RATE_LIMIT_WRITE_MAX_ATTEMPTS=30
RATE_LIMIT_WRITE_WINDOW_SECONDS=60
APP_ENV=development
TRUSTED_HOSTS=localhost,127.0.0.1
FORWARDED_ALLOW_IPS=127.0.0.1
```

In production, `CORS_ORIGINS` and `TRUSTED_HOSTS` are required and must not contain `*`. Set `AUTH_COOKIE_SECURE=true` when serving over HTTPS.

## Project Structure

```text
TestPaper-backend/
  alembic/
  scripts/
  tests/
  testpaper_backend/
    api/
      routes/
        auth.py
        drafts.py
        health.py
        images.py
        meta.py
        papers.py
        questions.py
        root.py
        tasks.py
        users.py
        websocket.py
    core/
      csrf.py
      errors.py
      factory.py
      http.py
      lifespan.py
      logging_config.py
      responses.py
    documents/
      paper_docx.py
      ExamPaperTemplate.docx
    schemas/
      auth.py
      common.py
      draft.py
      paper.py
      question.py
    services/
    worker/
    application.py
    config.py
    db.py
    main.py
    repositories.py
    security.py
  pyproject.toml
```

## API Overview

All application routes are under `/api/v1` except `GET /`. The canonical machine-readable contract is
[`contracts/openapi.json`](contracts/openapi.json); its export and compatibility policy are documented in
[`contracts/README.md`](contracts/README.md).

The canonical cross-platform repository strategy, runtime ownership, and dependency rules are defined in [TestPapers ADR-0001](https://github.com/Clearders/TestPapers/blob/main/docs/adr/0001-platform-repository-and-runtime-boundaries.md).

| Module | Prefix | Endpoints | Description |
| --- | --- | --- | --- |
| Root | `/` | 1 | Service info |
| Auth | `/api/v1/auth` | 9 | Login, register, refresh, logout, me, profile, password, avatar, account deletion |
| WebSocket | `/api/v1/ws` | 1 | Authenticated realtime events |
| Users | `/api/v1/users` | 4 | Admin user management |
| Questions | `/api/v1/questions` | 12 | Question CRUD, search, personal bank, revisions, corrections |
| Papers | `/api/v1/papers` | 11 | Paper CRUD, genetic generation, DOCX download, export preview, question management |
| Drafts | `/api/v1/drafts` | 11 | Shared paper drafts, collaborators, comments, review workflow, DOCX download |
| Images | `/api/v1/images` | 1 | Base64 PNG question image upload |
| Meta | `/api/v1/meta` | 2 | Subject and tag metadata |
| Tasks | `/api/v1/tasks` | 7 | Celery task dispatch and polling |
| Health | `/api/v1/health` | 2 | PostgreSQL and Redis health checks |

## Authentication and CSRF

- Browser sessions use the HttpOnly `testpapers_session` Cookie.
- Non-browser clients may use `Authorization: Bearer <token>`.
- WebSocket accepts authentication through Cookie or `Authorization` header; tokens are not accepted in URLs.
- Cookie-authenticated mutation requests require `X-CSRF-Token` from the `testpapers_csrf` Cookie.
- `/auth/login` and `/auth/register` are CSRF-exempt.
- Session tokens expire after `SESSION_TTL_HOURS`; refresh rotates the token and deletes the old one.
- Login/register and write operations use separate rate-limit tiers.

## Permission Model

| Permission | admin | teacher | viewer |
| --- | --- | --- | --- |
| `questions:read` | yes | yes | yes |
| `questions:write` | yes | yes | no |
| `questions:delete` | yes | yes | no |
| `answers:read` | yes | yes | no |
| `papers:read` | yes | yes | yes |
| `papers:write` | yes | yes | no |
| `users:manage` | yes | no | no |

Question and paper write operations also enforce ownership where applicable. Admins bypass owner checks; teachers can mutate their own content.

Shared drafts use paper permissions plus draft-level roles:

- Creating a shared draft requires `papers:write`.
- Listing, reading, commenting on, and downloading accessible drafts require `papers:read`.
- Owners and admins can rename, delete, change any review status, and manage collaborators.
- Editors can update draft content and move a draft to `in_review`.
- Viewers can read and comment but cannot edit content.
- Admins can access every shared draft.
- Draft approval is blocked until all open draft comments are resolved.

## Security Headers

- Backend API responses emit a restrictive CSP for API surfaces.
- The backend CSP does not include `unsafe-inline`, WebSocket schemes, or CDN hosts.
- The Nuxt frontend owns its nonce-based SSR CSP; do not replace it at the proxy/backend layer with a static CSP.
- HSTS is enabled only when `APP_ENV=production` and `AUTH_COOKIE_SECURE=true`.

## Main Capabilities

- User registration, login, session refresh, profile updates, password change, avatar upload, and account deletion.
- Admin user management.
- Question search, filtering, CRUD, personal bank, revision history, correction workflow, and PNG image attachment.
- Manual paper assembly with question add/remove, full list replacement, ordering, and marks.
- Genetic algorithm paper generation with multi-type targets, multi-subject candidate filtering, difficulty coefficient, required/preferred tags, and own-question filtering.
- DOCX export with images, Word-compatible math, answer visibility, question ordering, and layout density controls.
- Shared paper drafts with optimistic revision checks, collaborator roles, comments, review statuses, and cloud draft DOCX download.
- Celery tasks for worker ping, paper export, question validation, session cleanup, and stats.
- Realtime WebSocket broadcasts for question, paper, and shared draft changes.

## Response Format

Successful JSON response:

```json
{
  "success": true,
  "data": {},
  "meta": {
    "requestId": "550e8400-e29b-41d4-a716-446655440000"
  }
}
```

Error response:

```json
{
  "success": false,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Request validation failed",
    "details": [
      { "field": "difficulty", "reason": "must be easy, medium, or hard" }
    ]
  },
  "meta": {
    "requestId": "550e8400-e29b-41d4-a716-446655440000"
  }
}
```

`204 No Content` endpoints return no body. DOCX download endpoints return binary content directly and declare their media type and response headers in OpenAPI.

Cloud draft downloads use `GET /api/v1/drafts/{draft_public_id}/download`. The response is a DOCX binary built from the draft's stored `state.paper` snapshot, `state.exportMode`, `state.layoutDensity`, and `state.includeAnswersInExport`. Answers are included only when the draft asks for them and the caller has `answers:read`. The endpoint does not create or update a saved paper and returns `X-Cloud-Draft-Export: true`.

## WebSocket Events

| Event | Trigger |
| --- | --- |
| `auth.connected` | Client successfully connects |
| `question.created` | Question created |
| `question.updated` | Question updated |
| `question.deleted` | Question deleted |
| `paper.created` | Paper created manually or by generation |
| `paper.updated` | Paper metadata updated |
| `paper.questions.added` | Questions added to paper |
| `paper.question.removed` | Question removed from paper |
| `paper.questions.reordered` | Paper question order changed |
| `draft.updated` | Shared draft created, edited, renamed, or sharing changed |
| `draft.deleted` | Shared draft deleted |
| `draft.review.updated` | Shared draft review status changed |
| `draft.comment.created` | Shared draft comment added |
| `draft.comment.updated` | Shared draft comment edited or resolved |
| `pong` | Reply to client `{ "event": "ping" }` |

Per-IP WebSocket limit: 10 concurrent connections.

## Database Migrations

Run all migrations:

```bash
alembic upgrade head
```

Validate migration upgrade/downgrade structure without a running database:

```bash
python scripts/simulate_migrations.py
```

Current migration history has 15 versions, from initial users/questions/papers/auth-token tables through public IDs, search indexes, revisions/corrections, profile fields, paper ownership, disabling unchanged legacy demo accounts, and the July 2 shared paper draft tables.

The July 2 collaborative draft release adds `paper_drafts`, `paper_draft_collaborators`, and `paper_draft_comments` in revision `20260702_0015`. Run `alembic upgrade head` before deploying frontend code that calls `/api/v1/drafts`.

Fresh databases do not receive default users. Run `python scripts/bootstrap_admin.py` after migrating to create the first administrator. The script can also read `TESTPAPER_ADMIN_USERNAME`, `TESTPAPER_ADMIN_DISPLAY_NAME`, and `TESTPAPER_ADMIN_PASSWORD` for non-interactive provisioning.

## Production Deployment

See [../DEPLOYMENT-debian-production.md](../DEPLOYMENT-debian-production.md) for the full Debian deployment guide covering PostgreSQL, Redis, Nginx, systemd services, environment configuration, migrations, updates, and rollback.
