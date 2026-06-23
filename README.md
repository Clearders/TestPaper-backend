# TestPaper Backend

> **版本**: 0.1.0
> **框架**: FastAPI 0.136
> **Python**: 3.14+
> **最后更新**: 2026-06-21

FastAPI backend for the TestPapers test paper management and auto-generation system. The service exposes REST and WebSocket APIs, uses PostgreSQL for persistence, Redis for cache/Celery broker, and Celery for asynchronous jobs.

## Tech Stack

| Technology | Purpose |
|---|---|
| Python 3.14+ | Runtime |
| FastAPI | Web framework (REST + WebSocket) |
| SQLAlchemy 2.0 | ORM with session support |
| PostgreSQL | Primary database (JSONB columns for tags/options/images/subjects) |
| Alembic | Database migration management (14 versions) |
| Redis | Caching, Celery message broker, session rate-limiting |
| Celery | Async task queue (exports, validation, stats, cleanup) |
| Argon2-cffi | Password hashing (argon2id + pbkdf2_sha256 fallback) |
| Uvicorn | ASGI server |

## Project Structure

```text
TestPaper-backend/
  alembic/                       # Database migrations (14 versions)
    versions/
      20260507_0001_initial_schema.py
      20260508_0002_personal_questions_and_images.py
      20260509_0003_json_columns_to_jsonb.py
      20260511_0004_question_score_weight.py
      20260511_0005_identity_ids.py
      20260513_0006_question_search_indexes.py
      20260609_0007_add_users_public_id.py
      20260609_0008_add_questions_papers_public_id.py
      20260611_0009_split_choice_type.py
      20260611_0010_subject_to_subjects_array.py
      20260612_0011_add_revisions_and_corrections.py
      20260612_0012_add_user_profile_fields.py
      20260614_0013_add_papers_owner_id.py
      20260614_0014_disable_demo_accounts.py
  scripts/                       # Bootstrap and smoke-test helpers
  tests/                         # Automated tests
  testpaper_backend/
    application.py               # FastAPI app assembly (routes, middleware, static mounts)
    config.py                    # Environment-backed configuration
    db.py                        # SQLAlchemy models, engine, and session factory
    schemas/
      __init__.py
      auth.py         # Auth-related schema definitions
      common.py       # Shared Pydantic models (Envelope, MetaInfo)
      paper.py        # Paper creation, generation, export schema definitions
      question.py     # Question CRUD, revision, correction schema definitions
    repositories.py              # Database-backed store adapters
    security.py                  # Auth, password hashing, and permission helpers
    time_utils.py                # UTC datetime helpers
    redis_client.py              # Redis client lifecycle helpers
    api/
      router.py                  # API router composition
      dependencies.py            # FastAPI dependency aliases (permission checks)
      routes/
        auth.py                  # Login, register, refresh, logout, me, profile, password, avatar, account
        users.py                 # User CRUD (admin only via publicId)
        questions.py             # Question CRUD, search, personal bank, revisions, corrections
        papers.py                # Paper CRUD, genetic generation, DOCX download, export preview
        images.py                # Base64 PNG image upload
        meta.py                  # Subjects and tags metadata
        tasks.py                 # Celery task dispatch and polling
        health.py                # PostgreSQL and Redis health checks
        websocket.py             # Authenticated realtime WebSocket (CORS + query-param token)
        root.py                  # Service info endpoint
    core/
      factory.py                 # FastAPI factory with CORS, CSRF, and TrustedHost middleware
      http.py                    # Request ID, security headers, and exception handlers
      lifespan.py                # Startup/shutdown resource handling
      responses.py               # Response envelope helpers
      csrf.py                    # CSRF token generation, Cookie helpers, and middleware
    services/
      auth_sessions.py           # Cookie-based session management (TTL, rotate-on-refresh)
      images.py                  # Image storage (PNG validation, 30MB limit)
      paper_generation.py        # Genetic algorithm auto paper generation (multi-type, multi-subject)
      paper_create.py            # Paper creation from payload / genetic result
      papers.py                  # Paper query, export, question ordering, ownership enforcement
      profiles.py                # Avatar storage (PNG validation, 500KB limit)
      questions.py               # Question query, validation, update, revisions, corrections
      rate_limit.py              # Rate limiting for login/register/write endpoints
      realtime.py                # WebSocket connection manager (broadcast, per-IP limits)
      task_access.py             # Task access control helpers
      users.py                   # User CRUD and management
    documents/
      paper_docx.py              # Self-built DOCX generator (OOXML manipulation with LaTeX, images, auto layout)
      ExamPaperTemplate.docx     # DOCX export template
    worker/
      celery_app.py              # Celery app configuration
      tasks.py                   # Celery task definitions (ping, export, validate, stats, cleanup)
  pyproject.toml
  alembic.ini
  uv.lock
```

## Local Setup

```bash
# Install dependencies (requires uv)
uv sync

# Or with pip
pip install -e .
```

## Local Commands

```bash
# API server
testpaper-backend

# Alternative: uvicorn with hot reload
uvicorn testpaper_backend.application:app --reload

# Celery worker
celery -A testpaper_backend.worker.celery_app:celery worker --loglevel=info

# Database migrations
alembic upgrade head

# Create or reset the first administrator (prompts for a password)
python scripts/bootstrap_admin.py

# Lint and tests
ruff check .
pytest
```

## Environment Variables

### Required

```bash
DATABASE_URL=postgresql+psycopg://postgres:password@localhost:5432/testpapers
```

### Optional

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

In production (`APP_ENV=production`), `CORS_ORIGINS` and `TRUSTED_HOSTS` are required and must not contain `*`; the backend fails startup instead of silently allowing all origins or hosts. Example same-origin deployment values:

```text
APP_ENV=production
CORS_ORIGINS=https://testpapers.example.com,http://testpapers.example.com
TRUSTED_HOSTS=testpapers.example.com
AUTH_COOKIE_SECURE=true
```

## API Overview

All application routes are under `/api/v1`. For the full API specification, see [TestPapers/docs/api-spec.md](../TestPapers/docs/api-spec.md).

| Module | Prefix | Endpoints | Description |
|---|---|---|---|
| Root | `/` | 1 | Service info (`GET /` returns version and service name) |
| Auth | `/api/v1/auth` | 9 | Login, register, refresh, logout, me, profile, password, avatar, account |
| WebSocket | `/api/v1/ws` | 1 | Authenticated realtime events (token via Bearer or Cookie) |
| Users | `/api/v1/users` | 4 | Admin user management (CRUD via publicId) |
| Questions | `/api/v1/questions` | 12 | Question CRUD, search, personal bank, revisions, corrections |
| Papers | `/api/v1/papers` | 10 | Paper CRUD, genetic algorithm generation, DOCX download, export preview, question management |
| Images | `/api/v1/images` | 1 | Base64 PNG image upload (30MB, question illustrations) |
| Meta | `/api/v1/meta` | 2 | Available subjects and tags (distinct from questions) |
| Tasks | `/api/v1/tasks` | 7 | Celery task dispatch and status polling (ping, export, validate, stats, cleanup) |
| Health | `/api/v1/health` | 2 | PostgreSQL and Redis health checks (unauthenticated) |

## Authentication

- Uses HttpOnly Cookie (`testpapers_session`) for browser-based authentication
- `Authorization: Bearer <token>` is supported as a fallback for non-browser clients
- WebSocket accepts token via Cookie or `Authorization` header; tokens are never accepted in URLs
- CSRF protection via `testpapers_csrf` Cookie + `X-CSRF-Token` header for non-safe methods
- `/auth/login` and `/auth/register` exempt from CSRF checks
- Requests authenticated with an explicit `Authorization: Bearer` header do not require Cookie CSRF protection
- Session tokens expire after 12 hours (configurable via `SESSION_TTL_HOURS`)
- Expired tokens trigger `TOKEN_EXPIRED` (401); clients call `POST /api/v1/auth/refresh` to rotate
- Refresh rotates the token (old token deleted, new token issued)
- Login and register endpoints are rate-limited (configurable via `RATE_LIMIT_*` env vars)
- Write operations are rate-limited separately (configurable via `RATE_LIMIT_WRITE_*` env vars)

## User Profile Features

- **Profile update**: `PATCH /api/v1/auth/profile` — change username (max once per 30 days) or display name
- **Password change**: `PUT /api/v1/auth/password` — requires current password verification, clears all other sessions
- **Avatar upload**: `POST /api/v1/auth/avatar` — Base64 PNG, max 500KB, stored on disk
- **Account deletion**: `DELETE /api/v1/auth/account` — soft delete (sets `isActive=false`), clears all sessions

## Permission Model

| Permission | admin | teacher | viewer |
|---|---|---|---|
| `questions:read` | ✓ | ✓ | ✓ |
| `questions:write` | ✓ | ✓ | ✗ |
| `questions:delete` | ✓ | ✓ (own) | ✗ |
| `answers:read` | ✓ | ✓ | ✗ |
| `papers:read` | ✓ | ✓ | ✓ |
| `papers:write` | ✓ | ✓ | ✗ |
| `users:manage` | ✓ | ✗ | ✗ |

The `answers:read` permission controls whether the `answer` field is returned in question/paper responses. Papers also enforce ownership-based access control for write operations.

## WebSocket Events

The WebSocket endpoint broadcasts the following events to all connected clients:

| Event | Trigger |
|---|---|
| `auth.connected` | Client successfully connects (includes user info + server time) |
| `question.created` | New question created |
| `question.updated` | Question modified |
| `question.deleted` | Question removed |
| `paper.created` | New paper created (manual or genetic) |
| `paper.updated` | Paper metadata updated |
| `paper.questions.added` | Questions added to paper |
| `paper.question.removed` | Question removed from paper |
| `paper.questions.reordered` | Question order changed |
| `pong` | Response to client `{ "event": "ping" }` |

Per-IP connection limit: 10 concurrent WebSocket connections.

## Genetic Algorithm Paper Generation

The `POST /api/v1/papers/generate` endpoint uses a genetic algorithm to automatically select questions and assemble a paper. Key features:

- Multi-type support: specify multiple `questionTypes` with individual counts
- Multi-subject support: filter candidate pool by one or more `subjects`
- Filters candidate pool by subject(s), required tags, and optional owner (own questions only)
- Derives target difficulty distribution from the `difficultyCoefficient` parameter (0–1)
- Uses `preferredTags` for fitness scoring — questions matching more preferred tags score higher
- Allocates marks based on each question's `scoreWeight` field
- Returns detailed diagnostics: fitness score, candidate count, generation count, difficulty/type distribution
- Single-type adjustments: if a question type has fewer available candidates than requested, it is automatically adjusted

## DOCX Export

The `GET /api/v1/papers/{publicId}/download` endpoint generates a professional Word document using self-built OOXML manipulation. Features:

- LaTeX formulas rendered inside the DOCX using Word-compatible OMML (Office Math Markup Language)
- Question illustrations (PNG images) embedded inline
- Two question ordering modes: `paper` (as arranged) or `categorized` (grouped by question type)
- Configurable layout density: `auto` (detected from content), `normal`, `compact`, or `dense`
- Answer key included when the user has the `answers:read` permission
- Marks displayed per question in the document header

Export preview is available via `POST /api/v1/papers/{publicId}/export-preview` before downloading.

## Question Revisions and Corrections

- **Revisions**: Every `PATCH /api/v1/questions/{publicId}` automatically creates a revision record with field-level change summary. Revisions can be listed and deleted.
- **Corrections**: Any authenticated user can submit corrections (`wrong_answer`, `unclear`, `typo`, `other`). Question owners or admins can accept/reject corrections. Corrections can be listed and deleted.

## Question and Paper Ownership

- Questions have an `ownerId` field referencing the creator
- Papers have an `ownerId` field referencing the creator
- Teachers can only modify/delete their own questions and papers (admins bypass this restriction)
- Admin users can assign questions to other users via `users:manage` permission
- `GET /api/v1/questions/mine` returns only the current user's questions

## Rate Limiting

Three separate rate limit tiers control abuse prevention:

| Tier | Env Vars | Scope |
|---|---|---|
| Login | `RATE_LIMIT_MAX_ATTEMPTS`, `RATE_LIMIT_WINDOW_SECONDS` | `/auth/login` endpoint |
| Register | `RATE_LIMIT_MAX_ATTEMPTS`, `RATE_LIMIT_WINDOW_SECONDS` | `/auth/register` endpoint |
| Write | `RATE_LIMIT_WRITE_MAX_ATTEMPTS`, `RATE_LIMIT_WRITE_WINDOW_SECONDS` | All POST/PATCH/PUT/DELETE mutations |

## Response Format

All endpoints return a unified JSON envelope:

```json
{
  "success": true,
  "data": { },
  "meta": {
    "requestId": "550e8400-e29b-41d4-a716-446655440000"
  }
}
```

On error:

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

## Database Migrations

Run all migrations:

```bash
alembic upgrade head
```

Validate the complete upgrade/downgrade structure without a running database:

```bash
python scripts/simulate_migrations.py
```

Current migration history (14 versions):

| Version | Description |
|---|---|
| `0001` | Initial schema (users, questions, papers, auth_tokens) |
| `0002` | Personal questions + image columns |
| `0003` | JSON columns to JSONB |
| `0004` | Question score_weight |
| `0005` | Public identity IDs (uuid) |
| `0006` | Question search indexes |
| `0007` | Users public_id |
| `0008` | Questions/papers public_id |
| `0009` | Split choice type (single_choice, multiple_choice) |
| `0010` | Subject to subjects array |
| `0011` | Add revisions and corrections tables |
| `0012` | Add user profile fields (avatar_url, last_username_changed_at) |
| `0013` | Add paper ownership (owner_id to papers) |
| `0014` | Disable unchanged legacy demo accounts |

Fresh databases do not receive default users. Run `python scripts/bootstrap_admin.py`
after migrating to create the first administrator. The script can also read
`TESTPAPER_ADMIN_USERNAME`, `TESTPAPER_ADMIN_DISPLAY_NAME`, and
`TESTPAPER_ADMIN_PASSWORD` for non-interactive provisioning.

## Production Deployment

See [DEPLOYMENT-debian-production.md](../DEPLOYMENT-debian-production.md) in the project root for a complete Debian production guide covering:

- System package installation (PostgreSQL, Redis, Nginx)
- Backend as systemd services (FastAPI + Celery worker + Celery beat)
- Environment file setup with production security settings
- Database provisioning and migration
- Nginx reverse proxy configuration
- Update and rollback procedures
