# TestPaper Backend

FastAPI backend for the TestPapers test paper management and auto-generation system. The service exposes REST and WebSocket APIs, uses PostgreSQL for persistence, Redis for cache/health checks/Celery broker, and Celery for asynchronous jobs.

## Tech Stack

| Technology | Purpose |
|---|---|
| Python 3.14+ | Runtime |
| FastAPI | Web framework (REST + WebSocket) |
| SQLAlchemy 2.0 | ORM with async session support |
| PostgreSQL | Primary database (JSONB columns for tags/options/images) |
| Alembic | Database migration management |
| Redis | Caching, Celery message broker, health checks |
| Celery | Async task queue (exports, validation, stats) |
| Argon2-cffi | Password hashing |
| Uvicorn | ASGI server |

## Project Structure

```text
TestPaper-backend/
  alembic/                       # Database migrations (7 versions)
  scripts/                       # Smoke-test helpers
  tests/                         # Automated tests
  testpaper_backend/
    application.py               # FastAPI app assembly
    config.py                    # Environment-backed configuration
    db.py                        # SQLAlchemy models, engine, and session factory
    schemas.py                   # Pydantic API/domain schemas
    repositories.py              # Database-backed store adapters
    security.py                  # Auth, password hashing, and permission helpers
    time_utils.py                # UTC datetime helpers
    redis_client.py              # Redis client lifecycle helpers
    api/
      router.py                  # API router composition
      dependencies.py            # FastAPI dependency aliases
      routes/                    # Route modules by resource
        auth.py                  # Login, register, session refresh, logout
        users.py                 # User CRUD (admin only)
        questions.py             # Question CRUD, search, personal bank
        papers.py                # Paper CRUD, genetic generation, DOCX export
        images.py                # Base64 PNG image upload
        meta.py                  # Subjects and tags metadata
        tasks.py                 # Celery task dispatch and polling
        health.py                # PostgreSQL and Redis health checks
        websocket.py             # Authenticated realtime WebSocket
        root.py                  # Service info endpoint
    core/
      factory.py                 # FastAPI factory and middleware setup
      http.py                    # Request IDs and exception handlers
      lifespan.py                # Startup/shutdown resource handling
      responses.py               # Response envelope helpers
      csrf.py                    # CSRF token generation and Cookie helpers
    services/                    # Business logic by domain
      questions.py               # Question query, validation, update
      papers.py                  # Paper query, export, helper functions
      paper_generation.py        # Genetic algorithm auto paper generation
      auth_sessions.py           # Cookie-based session management
      images.py                  # Image storage service
      realtime.py                # WebSocket connection management
    documents/
      paper_docx.py              # Self-built DOCX generator (OOXML manipulation)
      ExamPaperTemplate.docx     # DOCX export template
    worker/
      celery_app.py              # Celery app configuration
      tasks.py                   # Celery task definitions
```

## Local Setup

```bash
pip install -e .
```

This installs `testpaper-backend`, `uvicorn`, `celery`, and `alembic` as console scripts.

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
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0
CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
AUTH_COOKIE_NAME=testpapers_session
AUTH_COOKIE_SECURE=false
AUTH_COOKIE_SAMESITE=lax
```

## API Overview

All application routes are under `/api/v1`. For the full API specification, see [TestPapers/docs/api-spec.md](../TestPapers/docs/api-spec.md).

| Module | Prefix | Description |
|---|---|---|
| Root | `/` | Service info (`GET /` returns version and service name) |
| Auth | `/api/v1/auth` | Login, register, session refresh, logout, current user |
| WebSocket | `/api/v1/ws` | Authenticated realtime events |
| Users | `/api/v1/users` | Admin user management (CRUD) |
| Questions | `/api/v1/questions` | Question bank CRUD, search, personal bank |
| Papers | `/api/v1/papers` | Paper CRUD, genetic algorithm generation, DOCX download |
| Images | `/api/v1/images` | Base64 PNG image upload |
| Meta | `/api/v1/meta` | Available subjects and tags |
| Tasks | `/api/v1/tasks` | Celery task dispatch and status polling |
| Health | `/api/v1/health` | PostgreSQL and Redis health checks |

## Authentication

- Uses HttpOnly Cookie (`testpapers_session`) for browser-based authentication
- `Authorization: Bearer <token>` is supported as a fallback for non-browser clients
- CSRF protection via `testpapers_csrf` Cookie + `X-CSRF-Token` header for non-safe methods (POST, PATCH, PUT, DELETE)
- Session tokens expire after 12 hours
- Expired tokens trigger `TOKEN_EXPIRED` (401), clients should call `POST /api/v1/auth/refresh` to rotate

## Permission Model

| Permission | admin | teacher | viewer |
|---|---|---|---|
| `questions:read` | ✓ | ✓ | ✓ |
| `questions:write` | ✓ | ✓ | ✗ |
| `questions:delete` | ✓ | ✗ | ✗ |
| `answers:read` | ✓ | ✓ | ✗ |
| `papers:read` | ✓ | ✓ | ✓ |
| `papers:write` | ✓ | ✓ | ✗ |
| `users:manage` | ✓ | ✗ | ✗ |

The `answers:read` permission controls whether the `answer` field is returned in question/paper responses.

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

## Genetic Algorithm Paper Generation

The `POST /api/v1/papers/generate` endpoint uses a genetic algorithm to automatically select questions and assemble a paper. Key features:

- Filters candidate pool by subject, question type, required tags, and optional owner (own questions only)
- Derives target difficulty distribution from the `difficultyCoefficient` parameter
- Uses `preferredTags` for fitness scoring — questions matching more preferred tags score higher
- Allocates marks based on each question's `scoreWeight` field
- Returns detailed diagnostics: fitness score, candidate count, generation count, difficulty/type distribution

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
    "message": "Request validation failed"
  },
  "meta": {
    "requestId": "550e8400-e29b-41d4-a716-446655440000"
  }
}
```
