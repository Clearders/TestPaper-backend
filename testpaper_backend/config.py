from __future__ import annotations

import logging
import os
import re
from pathlib import Path, PurePosixPath
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

AppEnvironment = Literal["local", "development", "test", "staging", "production"]
APP_ENVIRONMENTS: tuple[AppEnvironment, ...] = ("local", "development", "test", "staging", "production")
_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}
_OBJECT_STORAGE_REQUIRED = (
    "OBJECT_STORAGE_ENDPOINT",
    "OBJECT_STORAGE_BUCKET",
    "OBJECT_STORAGE_ACCESS_KEY",
    "OBJECT_STORAGE_SECRET_KEY",
)


class ConfigurationError(RuntimeError):
    """Raised when one or more environment settings are invalid."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("Configuration is invalid:\n- " + "\n- ".join(errors))


def _strict_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    raise RuntimeError(f"{name} must be one of: true, false, 1, 0, yes, no, on, off.")


def _positive_int(name: str, default: int, *, maximum: int | None = None) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer.") from exc
    if value < 1 or (maximum is not None and value > maximum):
        qualifier = f" between 1 and {maximum}" if maximum is not None else " a positive integer"
        raise RuntimeError(f"{name} must be{qualifier}.")
    return value


def _positive_float(name: str, default: float) -> float:
    raw = os.getenv(name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number.") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be a positive number.")
    return value


def normalize_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
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
        if get_app_env() in {"staging", "production"}:
            raise RuntimeError("CORS_ORIGINS is required in staging and production.")
        return DEFAULT_CORS_ORIGINS
    origins = [origin.strip() for origin in raw_origins.split(",") if origin.strip()]
    if not origins:
        raise RuntimeError("CORS_ORIGINS must contain at least one origin.")
    if get_app_env() in {"staging", "production"} and "*" in origins:
        raise RuntimeError("CORS_ORIGINS cannot contain '*' in staging or production.")
    return origins


def get_api_host() -> str:
    return os.getenv("API_HOST", DEFAULT_API_HOST)


def get_api_port() -> int:
    return _positive_int("API_PORT", DEFAULT_API_PORT, maximum=65535)


def get_redis_url() -> str:
    return os.getenv("REDIS_URL", DEFAULT_REDIS_URL)


def get_redis_max_connections() -> int:
    return _positive_int("REDIS_MAX_CONNECTIONS", DEFAULT_REDIS_MAX_CONNECTIONS)


def get_redis_socket_timeout() -> float:
    return _positive_float("REDIS_SOCKET_TIMEOUT", DEFAULT_REDIS_SOCKET_TIMEOUT)


def get_redis_socket_connect_timeout() -> float:
    return _positive_float("REDIS_SOCKET_CONNECT_TIMEOUT", DEFAULT_REDIS_SOCKET_CONNECT_TIMEOUT)


def get_auth_cookie_name() -> str:
    return os.getenv("AUTH_COOKIE_NAME", "testpapers_session")


def get_auth_cookie_domain() -> str | None:
    return os.getenv("AUTH_COOKIE_DOMAIN") or None


def get_auth_cookie_secure() -> bool:
    return _strict_bool("AUTH_COOKIE_SECURE", is_production())


def get_auth_cookie_samesite() -> Literal["lax", "strict", "none"]:
    value = os.getenv("AUTH_COOKIE_SAMESITE", "lax").lower()
    if value not in {"lax", "strict", "none"}:
        raise RuntimeError("AUTH_COOKIE_SAMESITE must be one of: lax, strict, none.")
    return value  # type: ignore[return-value]


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


def get_app_env() -> AppEnvironment:
    primary = os.getenv("TESTPAPERS_ENV")
    compatibility = os.getenv("APP_ENV")
    if primary and compatibility and primary.strip().lower() != compatibility.strip().lower():
        raise RuntimeError("TESTPAPERS_ENV and APP_ENV conflict; set only one or give them the same value.")
    value = (primary or compatibility or "development").strip().lower()
    if value not in APP_ENVIRONMENTS:
        raise RuntimeError(f"TESTPAPERS_ENV must be one of: {', '.join(APP_ENVIRONMENTS)}.")
    return value  # type: ignore[return-value]


def is_production() -> bool:
    return get_app_env() == "production"


def get_csrf_cookie_name() -> str:
    return os.getenv("CSRF_COOKIE_NAME", "testpapers_csrf")


def get_trusted_hosts() -> list[str]:
    raw = os.getenv("TRUSTED_HOSTS")
    if not raw:
        if get_app_env() in {"staging", "production"}:
            raise RuntimeError("TRUSTED_HOSTS is required in staging and production.")
        logger.warning("TRUSTED_HOSTS not configured, defaulting to '*' which disables host validation")
        return ["*"]
    hosts = [host.strip() for host in raw.split(",") if host.strip()]
    if not hosts:
        raise RuntimeError("TRUSTED_HOSTS must contain at least one host.")
    if get_app_env() in {"staging", "production"} and "*" in hosts:
        raise RuntimeError("TRUSTED_HOSTS cannot contain '*' in staging or production.")
    return hosts


def get_session_ttl_hours() -> int:
    return _positive_int("SESSION_TTL_HOURS", 12)


def get_rate_limit_max_attempts() -> int:
    return _positive_int("RATE_LIMIT_MAX_ATTEMPTS", 5)


def get_rate_limit_window_seconds() -> int:
    return _positive_int("RATE_LIMIT_WINDOW_SECONDS", 60)


def get_forwarded_allow_ips() -> str:
    return os.getenv("FORWARDED_ALLOW_IPS", "127.0.0.1")


def get_rate_limit_write_max_attempts() -> int:
    return _positive_int("RATE_LIMIT_WRITE_MAX_ATTEMPTS", 30)


def get_rate_limit_write_window_seconds() -> int:
    return _positive_int("RATE_LIMIT_WRITE_WINDOW_SECONDS", 60)


def get_data_dir() -> Path:
    raw = os.getenv("DATA_DIR", ".runtime")
    path = Path(raw).expanduser()
    if get_app_env() in {"staging", "production"} and not (path.is_absolute() or PurePosixPath(raw).is_absolute()):
        raise RuntimeError("DATA_DIR must be an absolute path in staging and production.")
    return path.resolve()


def _runtime_subdirectory(name: str, default: str) -> Path:
    raw = os.getenv(name)
    path = Path(raw).expanduser() if raw else get_data_dir() / default
    return path.resolve()


def get_image_upload_dir() -> Path:
    return _runtime_subdirectory("IMAGE_UPLOAD_DIR", "images")


def get_avatar_upload_dir() -> Path:
    return _runtime_subdirectory("AVATAR_UPLOAD_DIR", "avatars")


def get_object_storage_settings() -> dict[str, str | bool] | None:
    configured = {name: os.getenv(name, "").strip() for name in _OBJECT_STORAGE_REQUIRED}
    present = [name for name, value in configured.items() if value]
    if not present:
        return None
    missing = [name for name, value in configured.items() if not value]
    if missing:
        raise RuntimeError("Object storage is partially configured; missing: " + ", ".join(missing) + ".")
    return {
        "endpoint": configured["OBJECT_STORAGE_ENDPOINT"],
        "bucket": configured["OBJECT_STORAGE_BUCKET"],
        "access_key": configured["OBJECT_STORAGE_ACCESS_KEY"],
        "secret_key": configured["OBJECT_STORAGE_SECRET_KEY"],
        "secure": _strict_bool("OBJECT_STORAGE_SECURE", True),
    }


def validate_configuration(*, require_database: bool = True) -> dict[str, object]:
    """Validate independent settings and return a summary containing no secrets."""

    errors: list[str] = []
    values: dict[str, object] = {}

    def capture(label: str, getter):
        try:
            values[label] = getter()
        except RuntimeError as exc:
            errors.append(str(exc))

    capture("environment", get_app_env)
    if require_database:
        capture("database", get_database_url)
    capture("api_port", get_api_port)
    capture("cors_origins", get_cors_origins)
    capture("trusted_hosts", get_trusted_hosts)
    capture("cookie_secure", get_auth_cookie_secure)
    capture("cookie_samesite", get_auth_cookie_samesite)
    capture("data_dir", get_data_dir)
    capture("image_upload_dir", get_image_upload_dir)
    capture("avatar_upload_dir", get_avatar_upload_dir)
    capture("redis_max_connections", get_redis_max_connections)
    capture("redis_socket_timeout", get_redis_socket_timeout)
    capture("redis_socket_connect_timeout", get_redis_socket_connect_timeout)
    capture("session_ttl_hours", get_session_ttl_hours)
    capture("rate_limit_max_attempts", get_rate_limit_max_attempts)
    capture("rate_limit_window_seconds", get_rate_limit_window_seconds)
    capture("rate_limit_write_max_attempts", get_rate_limit_write_max_attempts)
    capture("rate_limit_write_window_seconds", get_rate_limit_write_window_seconds)
    capture("object_storage", get_object_storage_settings)

    environment = values.get("environment")
    if environment == "production" and values.get("cookie_secure") is False:
        errors.append("AUTH_COOKIE_SECURE must be true in production.")
    if environment in {"staging", "production"} and not os.getenv("CORS_ORIGINS"):
        errors.append("CORS_ORIGINS is required in staging and production.")
    if environment in {"staging", "production"} and not os.getenv("TRUSTED_HOSTS"):
        errors.append("TRUSTED_HOSTS is required in staging and production.")

    if errors:
        raise ConfigurationError(list(dict.fromkeys(errors)))

    object_storage = values.get("object_storage")
    return {
        "environment": environment,
        "api_port": values.get("api_port"),
        "data_dir": str(values.get("data_dir")),
        "image_upload_dir": str(values.get("image_upload_dir")),
        "avatar_upload_dir": str(values.get("avatar_upload_dir")),
        "database_configured": bool(values.get("database")) if require_database else bool(os.getenv("DATABASE_URL")),
        "redis_configured": bool(os.getenv("REDIS_URL")),
        "object_storage_configured": object_storage is not None,
    }
