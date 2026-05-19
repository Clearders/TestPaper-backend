# TestPaper Backend

FastAPI backend for TestPapers. The service exposes REST and WebSocket APIs, uses PostgreSQL for persistence, Redis for cache/health checks, and Celery for asynchronous jobs.

## Project Structure

```text
TestPaper-backend/
  alembic/                       # Database migrations
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
    core/
      factory.py                 # FastAPI factory and middleware setup
      http.py                    # Request IDs and exception handlers
      lifespan.py                # Startup/shutdown resource handling
      responses.py               # Response envelope helpers
    services/                    # Business logic by domain
    documents/
      paper_docx.py              # DOCX export generation
      ExamPaperTemplate.docx     # Packaged DOCX export template
    worker/
      celery_app.py              # Celery app configuration
      tasks.py                   # Celery task definitions
```

## Local Setup

Install the backend from this directory in an activated Python environment:

```bash
pip install -e .
```

The activated environment puts the installed `testpaper-backend`, `uvicorn`, `celery`, and `alembic` console scripts on `PATH`.

## Local Commands

```bash
# API
testpaper-backend

# Alternative uvicorn command
uvicorn testpaper_backend.application:app --reload

# Celery worker
celery -A testpaper_backend.worker.celery_app:celery worker --loglevel=info

# Database migrations
alembic upgrade head

# Lint and tests
ruff check .
pytest
```

## Required Environment

`DATABASE_URL` must point to PostgreSQL, for example:

```bash
DATABASE_URL=postgresql+psycopg://postgres:password@localhost:5432/testpapers
```

Optional settings:

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

## API Surface

All application routes are under `/api/v1`.

- `/auth` for login, registration, session refresh, and logout
- `/users` for administrator user management
- `/questions` for question bank CRUD and search
- `/papers` for paper CRUD, generation, preview, and DOCX download
- `/images` for PNG uploads
- `/meta` for subjects and tags
- `/tasks` for Celery task dispatch/status
- `/health` for Redis/PostgreSQL health checks
- `/ws` for authenticated realtime events
