from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from alembic.config import Config
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from alembic import command
from testpaper_backend.db import AuthAuditLogRow, AuthTokenRow, UserRow
from testpaper_backend.schemas import TokenType, UserRole
from testpaper_backend.security import token_digest
from testpaper_backend.services import auth_sessions
from testpaper_backend.time_utils import now_utc

POSTGRES_URL = os.getenv("MIGRATION_SMOKE_DATABASE_URL")


@pytest.mark.skipif(not POSTGRES_URL, reason="MIGRATION_SMOKE_DATABASE_URL is required for the PostgreSQL race test")
def test_refresh_token_is_single_use_under_concurrency(monkeypatch: pytest.MonkeyPatch) -> None:
    project_root = Path(__file__).resolve().parents[1]
    admin_url = make_url(POSTGRES_URL).update_query_dict({"connect_timeout": "5"})
    database_name = f"testpaper_refresh_race_{uuid4().hex[:12]}"
    test_url = admin_url.set(database=database_name)
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    test_engine = None
    database_created = False

    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        database_created = True

        monkeypatch.setenv("DATABASE_URL", test_url.render_as_string(hide_password=False))
        alembic_config = Config(project_root / "alembic.ini")
        command.upgrade(alembic_config, "head")

        test_engine = create_engine(test_url, pool_size=5, max_overflow=0)
        session_factory = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)
        monkeypatch.setattr(auth_sessions, "SessionLocal", session_factory)

        now = now_utc()
        raw_refresh = "refresh-race-secret"
        family_id = str(uuid4())
        with session_factory() as session:
            user = UserRow(
                public_id=str(uuid4()),
                username=f"race-{uuid4().hex[:12]}",
                display_name="Race test",
                password_hash="not-used",
                role=UserRole.viewer.value,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            session.add(user)
            session.flush()
            session.add(
                AuthTokenRow(
                    token=token_digest(raw_refresh),
                    user_id=user.id,
                    token_type=TokenType.refresh.value,
                    device_id="race-device",
                    device_name="Race device",
                    ip_address="127.0.0.1",
                    last_seen_at=now,
                    family_id=family_id,
                    created_at=now,
                    expires_at=now + timedelta(days=1),
                )
            )
            session.commit()

        barrier = Barrier(2)

        def rotate() -> str:
            barrier.wait(timeout=5)
            try:
                auth_sessions.refresh_token_pair(raw_refresh)
                return "success"
            except HTTPException as exc:
                return str(exc.detail["code"])

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = sorted(executor.map(lambda _: rotate(), range(2)))

        assert results == ["TOKEN_REUSED", "success"]
        with session_factory() as session:
            active_family_tokens = session.scalar(
                select(func.count())
                .select_from(AuthTokenRow)
                .where(
                    AuthTokenRow.family_id == family_id,
                    AuthTokenRow.revoked_at.is_(None),
                )
            )
            reuse_events = session.scalar(select(func.count()).select_from(AuthAuditLogRow).where(AuthAuditLogRow.event == "refresh_reuse"))
        assert active_family_tokens == 0
        assert reuse_events == 1
    finally:
        if test_engine is not None:
            test_engine.dispose()
        if database_created:
            with admin_engine.connect() as connection:
                connection.execute(
                    text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :database_name"),
                    {"database_name": database_name},
                )
                connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
        admin_engine.dispose()
