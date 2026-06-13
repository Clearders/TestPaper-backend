from __future__ import annotations

import logging
import os
import re
from typing import Literal

logger = logging.getLogger(__name__)

_REDIS_DB_PATTERN = re.compile(r"/(\d+)$")

DEFAULT_API_HOST = "0.0.0.0"
DEFAULT_API_PORT = 8000
DEFAULT_CORS_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3001",
]

DEFAULT_REDIS_URL = "redis://localhost:6379/0"
DEFAULT_REDIS_MAX_CONNECTIONS = 50
DEFAULT_REDIS_SOCKET_TIMEOUT = 5.0
DEFAULT_REDIS_SOCKET_CONNECT_TIMEOUT = 2.0
DEFAULT_CELERY_REDIS_DB = "1"


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


def get_redis_url() -> str:
    return os.getenv("REDIS_URL", DEFAULT_REDIS_URL)


def get_redis_max_connections() -> int:
    raw = os.getenv("REDIS_MAX_CONNECTIONS", str(DEFAULT_REDIS_MAX_CONNECTIONS))
    try:
        value = int(raw)
        if value < 1:
            raise ValueError
        return value
    except ValueError as exc:
        raise RuntimeError("REDIS_MAX_CONNECTIONS must be a positive integer.") from exc


def get_redis_socket_timeout() -> float:
    raw = os.getenv("REDIS_SOCKET_TIMEOUT", str(DEFAULT_REDIS_SOCKET_TIMEOUT))
    try:
        value = float(raw)
        if value <= 0:
            raise ValueError
        return value
    except ValueError as exc:
        raise RuntimeError("REDIS_SOCKET_TIMEOUT must be a positive number.") from exc


def get_redis_socket_connect_timeout() -> float:
    raw = os.getenv("REDIS_SOCKET_CONNECT_TIMEOUT", str(DEFAULT_REDIS_SOCKET_CONNECT_TIMEOUT))
    try:
        value = float(raw)
        if value <= 0:
            raise ValueError
        return value
    except ValueError as exc:
        raise RuntimeError("REDIS_SOCKET_CONNECT_TIMEOUT must be a positive number.") from exc


def get_auth_cookie_name() -> str:
    return os.getenv("AUTH_COOKIE_NAME", "testpapers_session")


def get_auth_cookie_domain() -> str | None:
    return os.getenv("AUTH_COOKIE_DOMAIN") or None


def get_auth_cookie_secure() -> bool:
    if "AUTH_COOKIE_SECURE" in os.environ:
        return os.environ["AUTH_COOKIE_SECURE"].lower() in {"1", "true", "yes", "on"}
    return is_production()


def get_auth_cookie_samesite() -> Literal["lax", "strict", "none"]:
    value = os.getenv("AUTH_COOKIE_SAMESITE", "lax").lower()
    if value == "strict":
        return "strict"
    if value == "none":
        return "none"
    return "lax"


def get_celery_broker_url() -> str:
    raw = os.getenv("CELERY_BROKER_URL")
    if raw:
        return raw
    base = os.getenv("REDIS_URL", DEFAULT_REDIS_URL)
    return _replace_redis_db(base, DEFAULT_CELERY_REDIS_DB)


def get_celery_result_backend_url() -> str:
    raw = os.getenv("CELERY_RESULT_BACKEND")
    if raw:
        return raw
    base = os.getenv("REDIS_URL", DEFAULT_REDIS_URL)
    return _replace_redis_db(base, DEFAULT_CELERY_REDIS_DB)


def _replace_redis_db(url: str, db: str) -> str:
    if _REDIS_DB_PATTERN.search(url):
        return _REDIS_DB_PATTERN.sub(f"/{db}", url)
    return url.rstrip("/") + "/" + db


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
        logger.warning("TRUSTED_HOSTS not configured, defaulting to '*' which disables host validation")
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


def get_rate_limit_max_attempts() -> int:
    raw = os.getenv("RATE_LIMIT_MAX_ATTEMPTS", "5")
    try:
        max_attempts = int(raw)
        if max_attempts < 1:
            raise ValueError
        return max_attempts
    except ValueError as exc:
        raise RuntimeError("RATE_LIMIT_MAX_ATTEMPTS must be a positive integer.") from exc


def get_rate_limit_window_seconds() -> int:
    raw = os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60")
    try:
        window = int(raw)
        if window < 1:
            raise ValueError
        return window
    except ValueError as exc:
        raise RuntimeError("RATE_LIMIT_WINDOW_SECONDS must be a positive integer.") from exc


def get_forwarded_allow_ips() -> str:
    return os.getenv("FORWARDED_ALLOW_IPS", "127.0.0.1")


def get_rate_limit_write_max_attempts() -> int:
    raw = os.getenv("RATE_LIMIT_WRITE_MAX_ATTEMPTS", "30")
    try:
        value = int(raw)
        if value < 1:
            raise ValueError
        return value
    except ValueError as exc:
        raise RuntimeError("RATE_LIMIT_WRITE_MAX_ATTEMPTS must be a positive integer.") from exc


def get_rate_limit_write_window_seconds() -> int:
    raw = os.getenv("RATE_LIMIT_WRITE_WINDOW_SECONDS", "60")
    try:
        value = int(raw)
        if value < 1:
            raise ValueError
        return value
    except ValueError as exc:
        raise RuntimeError("RATE_LIMIT_WRITE_WINDOW_SECONDS must be a positive integer.") from exc
