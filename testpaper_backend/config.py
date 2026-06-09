from __future__ import annotations

import os
from typing import Literal

DEFAULT_API_HOST = "0.0.0.0"
DEFAULT_API_PORT = 8000
DEFAULT_CORS_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3001",
]

DEFAULT_REDIS_URL = "redis://localhost:6379/0"


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
            raise RuntimeError("DATABASE_URL is required.")
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


def get_api_host() -> str:
    return os.getenv("API_HOST", DEFAULT_API_HOST)


def get_api_port() -> int:
    raw = os.getenv("API_PORT", str(DEFAULT_API_PORT))
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError("API_PORT must be an integer.") from exc


def get_redis_url(*, required: bool = False) -> str | None:
    raw = os.getenv("REDIS_URL")
    if not raw:
        if required:
            return DEFAULT_REDIS_URL
        return DEFAULT_REDIS_URL
    return raw


def get_auth_cookie_name() -> str:
    return os.getenv("AUTH_COOKIE_NAME", "testpapers_session")


def get_auth_cookie_domain() -> str | None:
    return os.getenv("AUTH_COOKIE_DOMAIN") or None


def get_auth_cookie_secure() -> bool:
    return os.getenv("AUTH_COOKIE_SECURE", "false").lower() in {"1", "true", "yes", "on"}


def get_auth_cookie_samesite() -> Literal["lax", "strict", "none"]:
    value = os.getenv("AUTH_COOKIE_SAMESITE", "lax").lower()
    if value == "strict":
        return "strict"
    if value == "none":
        return "none"
    return "lax"


def get_celery_broker_url() -> str:
    raw = os.getenv("CELERY_BROKER_URL") or os.getenv("REDIS_URL") or DEFAULT_REDIS_URL
    return raw


def get_celery_result_backend_url() -> str:
    raw = os.getenv("CELERY_RESULT_BACKEND") or os.getenv("REDIS_URL") or DEFAULT_REDIS_URL
    return raw


def get_app_env() -> str:
    value = os.getenv("APP_ENV", "development")
    if value in ("production", "prod"):
        return "production"
    return "development"


def is_production() -> bool:
    return get_app_env() == "production"


def get_csrf_cookie_name() -> str:
    return os.getenv("CSRF_COOKIE_NAME", "testpapers_csrf")


def get_trusted_hosts() -> list[str]:
    raw = os.getenv("TRUSTED_HOSTS")
    if not raw:
        return ["*"]
    return [host.strip() for host in raw.split(",") if host.strip()]


def get_session_ttl_hours() -> int:
    raw = os.getenv("SESSION_TTL_HOURS", "12")
    try:
        hours = int(raw)
        if hours < 1:
            raise ValueError
        return hours
    except ValueError as exc:
        raise RuntimeError("SESSION_TTL_HOURS must be a positive integer.") from exc
