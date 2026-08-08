# syntax=docker/dockerfile:1.7
FROM python:3.13-slim AS runtime

COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

RUN addgroup --system testpapers && adduser --system --ingroup testpapers --home /app testpapers
WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-install-project

COPY testpaper_backend ./testpaper_backend
COPY scripts ./scripts
COPY alembic ./alembic
COPY alembic.ini ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-editable

RUN mkdir -p /var/lib/testpapers && chown -R testpapers:testpapers /app /var/lib/testpapers
USER testpapers

ENV TESTPAPERS_ENV=development \
    DATA_DIR=/var/lib/testpapers

EXPOSE 8000
CMD ["testpaper-backend"]
