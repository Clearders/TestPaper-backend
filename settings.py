from __future__ import annotations

import os


DEFAULT_CORS_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3001",
]


def normalize_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def get_database_url(*, required: bool = True) -> str | None:
    raw_url = os.getenv("DATABASE_URL")
    if not raw_url:
        if required:
            raise RuntimeError("DATABASE_URL is required and must point to a PostgreSQL database.")
        return None

    normalized_url = normalize_database_url(raw_url)
    if not normalized_url.startswith("postgresql+psycopg://"):
        raise RuntimeError("Only PostgreSQL DATABASE_URL values are supported.")
    return normalized_url


def get_cors_origins() -> list[str]:
    raw_origins = os.getenv("CORS_ORIGINS")
    if not raw_origins:
        return DEFAULT_CORS_ORIGINS
    return [origin.strip() for origin in raw_origins.split(",") if origin.strip()]


def get_redis_url(*, required: bool = False) -> str | None:
    raw = os.getenv("REDIS_URL")
    if not raw:
        if required:
            raise RuntimeError("REDIS_URL is required for Redis/Celery features.")
        return None
    return raw


def get_celery_broker_url() -> str:
    raw = os.getenv("CELERY_BROKER_URL") or os.getenv("REDIS_URL")
    if not raw:
        raise RuntimeError("CELERY_BROKER_URL or REDIS_URL is required for Celery.")
    return raw


def get_celery_result_backend_url() -> str:
    raw = os.getenv("CELERY_RESULT_BACKEND") or os.getenv("REDIS_URL")
    if not raw:
        raise RuntimeError("CELERY_RESULT_BACKEND or REDIS_URL is required for Celery result backend.")
    return raw
