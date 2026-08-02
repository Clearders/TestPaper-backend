from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI

from testpaper_backend.config import (
    APP_ENVIRONMENTS,
    ConfigurationError,
    get_app_env,
    get_avatar_upload_dir,
    get_cors_origins,
    get_image_upload_dir,
    get_object_storage_settings,
    get_trusted_hosts,
    validate_configuration,
)
from testpaper_backend.core.lifespan import lifespan

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("environment", APP_ENVIRONMENTS)
def test_all_named_environments_are_accepted(monkeypatch: pytest.MonkeyPatch, environment: str) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("TESTPAPERS_ENV", environment)
    assert get_app_env() == environment


def test_app_env_alias_is_supported_but_conflicts_and_typos_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TESTPAPERS_ENV", raising=False)
    monkeypatch.setenv("APP_ENV", "test")
    assert get_app_env() == "test"
    monkeypatch.setenv("TESTPAPERS_ENV", "development")
    with pytest.raises(RuntimeError, match="conflict"):
        get_app_env()
    monkeypatch.delenv("APP_ENV")
    monkeypatch.setenv("TESTPAPERS_ENV", "dev")
    with pytest.raises(RuntimeError, match="local, development, test, staging, production"):
        get_app_env()


def test_runtime_directories_derive_from_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TESTPAPERS_ENV", "local")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("IMAGE_UPLOAD_DIR", raising=False)
    monkeypatch.setenv("AVATAR_UPLOAD_DIR", str(tmp_path / "custom-avatars"))
    assert get_image_upload_dir() == (tmp_path / "data" / "images").resolve()
    assert get_avatar_upload_dir() == (tmp_path / "custom-avatars").resolve()


def test_staging_and_production_require_absolute_data_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESTPAPERS_ENV", "staging")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("DATA_DIR", "relative-data")
    with pytest.raises(RuntimeError, match="absolute"):
        get_image_upload_dir()


def test_staging_rejects_wildcard_origins_and_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESTPAPERS_ENV", "staging")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("CORS_ORIGINS", "*")
    monkeypatch.setenv("TRUSTED_HOSTS", "*")
    with pytest.raises(RuntimeError, match="staging or production"):
        get_cors_origins()
    with pytest.raises(RuntimeError, match="staging or production"):
        get_trusted_hosts()


def test_object_storage_is_all_or_none(monkeypatch: pytest.MonkeyPatch) -> None:
    names = ("OBJECT_STORAGE_ENDPOINT", "OBJECT_STORAGE_BUCKET", "OBJECT_STORAGE_ACCESS_KEY", "OBJECT_STORAGE_SECRET_KEY")
    for name in names:
        monkeypatch.delenv(name, raising=False)
    assert get_object_storage_settings() is None
    monkeypatch.setenv("OBJECT_STORAGE_ENDPOINT", "http://localhost:9000")
    with pytest.raises(RuntimeError, match="OBJECT_STORAGE_BUCKET"):
        get_object_storage_settings()
    monkeypatch.setenv("OBJECT_STORAGE_BUCKET", "testpapers")
    monkeypatch.setenv("OBJECT_STORAGE_ACCESS_KEY", "access")
    monkeypatch.setenv("OBJECT_STORAGE_SECRET_KEY", "super-secret-value")
    assert get_object_storage_settings() == {
        "endpoint": "http://localhost:9000",
        "bucket": "testpapers",
        "access_key": "access",
        "secret_key": "super-secret-value",
        "secure": True,
    }


def test_preflight_aggregates_independent_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESTPAPERS_ENV", "production")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DATA_DIR", "relative")
    monkeypatch.setenv("API_PORT", "not-a-port")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    monkeypatch.delenv("TRUSTED_HOSTS", raising=False)
    with pytest.raises(ConfigurationError) as exc_info:
        validate_configuration()
    message = str(exc_info.value)
    assert "DATABASE_URL is required" in message
    assert "API_PORT must be an integer" in message
    assert "DATA_DIR must be an absolute path" in message
    assert "AUTH_COOKIE_SECURE must be true" in message
    assert len(exc_info.value.errors) >= 4


def test_asgi_lifespan_runs_full_preflight_before_startup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TESTPAPERS_ENV", "production")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:password@localhost/testpapers")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("CORS_ORIGINS", "https://testpapers.example.invalid")
    monkeypatch.setenv("TRUSTED_HOSTS", "api.testpapers.example.invalid")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")

    async def start_application() -> None:
        async with lifespan(FastAPI()):
            pass

    with pytest.raises(ConfigurationError, match="AUTH_COOKIE_SECURE must be true"):
        asyncio.run(start_application())


def _clean_config_environment() -> dict[str, str]:
    environment = os.environ.copy()
    names = ("APP_ENV", "DATABASE_URL", "DATA_DIR", "IMAGE_UPLOAD_DIR", "AVATAR_UPLOAD_DIR")
    for name in list(environment):
        if name in names or name.startswith(("TESTPAPERS_", "OBJECT_STORAGE_")):
            environment.pop(name)
    return environment


def test_config_cli_honors_exported_precedence_and_never_prints_secrets(tmp_path: Path) -> None:
    env_file = tmp_path / "test.env"
    env_file.write_text(
        "TESTPAPERS_ENV=local\nDATABASE_URL=postgresql+psycopg://user:file-secret@localhost/file_db\nDATA_DIR=.runtime/from-file\n",
        encoding="utf-8",
    )
    environment = _clean_config_environment()
    environment["DATABASE_URL"] = "postgresql+psycopg://user:exported-secret@localhost/exported_db"
    completed = subprocess.run(
        [sys.executable, "scripts/validate_config.py", "--env-file", str(env_file)],
        cwd=PROJECT_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout.split("\n", 1)[1])
    assert summary["environment"] == "local"
    assert summary["database_configured"] is True
    assert "file-secret" not in completed.stdout
    assert "exported-secret" not in completed.stdout
