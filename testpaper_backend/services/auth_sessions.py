from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta
from typing import cast

from fastapi import HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from testpaper_backend.config import (
    get_auth_cookie_domain,
    get_auth_cookie_name,
    get_auth_cookie_samesite,
    get_auth_cookie_secure,
    get_session_ttl_hours,
)
from testpaper_backend.db import AuthTokenRow, SessionLocal, UserRow
from testpaper_backend.schemas import AuthSession, LoginRequest, RegisterRequest, UserRole
from testpaper_backend.security import auth_error, password_hash, user_row_to_entity, verify_password
from testpaper_backend.time_utils import as_aware_utc, now_utc

logger = logging.getLogger(__name__)


def _session_ttl() -> timedelta:
    return timedelta(hours=get_session_ttl_hours())


def _username_exists() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": "USER_ALREADY_EXISTS", "message": "Username already exists"},
    )


def create_auth_session(session: Session, user_row: UserRow) -> tuple[str, AuthSession]:
    now = now_utc()
    session.query(AuthTokenRow).filter(AuthTokenRow.expires_at <= now).delete(synchronize_session=False)
    token = secrets.token_urlsafe(48)
    expires_at = now + _session_ttl()
    session.add(AuthTokenRow(token=token, user_id=user_row.id, created_at=now, expires_at=expires_at))
    session.commit()
    session.refresh(user_row)
    return token, AuthSession(expiresAt=expires_at, user=user_row_to_entity(user_row))


def authenticate_user(payload: LoginRequest) -> tuple[str, AuthSession]:
    username = payload.username.strip().lower()
    with SessionLocal() as session:
        user_row = session.scalars(select(UserRow).where(UserRow.username == username)).first()
        if user_row is None or not user_row.is_active:
            raise auth_error("INVALID_CREDENTIALS", "Invalid username or password")
        valid, needs_migration = verify_password(payload.password, user_row.password_hash)
        if not valid:
            raise auth_error("INVALID_CREDENTIALS", "Invalid username or password")

        logger.info("User login attempt for user: %s", user_row.public_id)

        if needs_migration:
            user_row.password_hash = password_hash(payload.password)
            session.commit()

        return create_auth_session(session, user_row)


def register_user(payload: RegisterRequest) -> tuple[str, AuthSession]:
    with SessionLocal() as session:
        existing = session.scalars(select(UserRow).where(UserRow.username == payload.username)).first()
        if existing is not None:
            raise _username_exists()

        now = now_utc()
        user_row = UserRow(
            username=payload.username,
            display_name=payload.displayName,
            password_hash=password_hash(payload.password),
            role=UserRole.viewer.value,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        session.add(user_row)
        try:
            session.flush()
            return create_auth_session(session, user_row)
        except IntegrityError as exc:
            session.rollback()
            raise _username_exists() from exc


def set_auth_cookie(response: Response, token: str, expires_at: datetime) -> None:
    max_age = max(0, int((expires_at - now_utc()).total_seconds()))
    response.set_cookie(
        key=get_auth_cookie_name(),
        value=token,
        max_age=max_age,
        expires=expires_at,
        path="/",
        domain=get_auth_cookie_domain(),
        secure=get_auth_cookie_secure(),
        httponly=True,
        samesite=get_auth_cookie_samesite(),
    )


def clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(
        key=get_auth_cookie_name(),
        path="/",
        domain=get_auth_cookie_domain(),
        secure=get_auth_cookie_secure(),
        httponly=True,
        samesite=get_auth_cookie_samesite(),
    )


def revoke_auth_session(token: str | None) -> None:
    if not token:
        return
    with SessionLocal() as session:
        token_row = session.get(AuthTokenRow, token)
        if token_row is not None:
            session.delete(token_row)
            session.commit()


def refresh_auth_session(token: str | None) -> tuple[str, AuthSession]:
    if not token:
        raise auth_error()

    with SessionLocal() as session:
        token_row = cast(AuthTokenRow | None, session.get(AuthTokenRow, token))
        if token_row is None:
            raise auth_error("INVALID_TOKEN", "Invalid or expired token")
        if as_aware_utc(token_row.expires_at) <= now_utc():
            session.delete(token_row)
            session.commit()
            raise auth_error("TOKEN_EXPIRED", "Token has expired")

        user_row = cast(UserRow | None, session.get(UserRow, token_row.user_id))
        if user_row is None or not user_row.is_active:
            session.delete(token_row)
            session.commit()
            raise auth_error("ACCOUNT_DISABLED", "Account is disabled")

        # Atomically delete old token and create new one within the same transaction
        session.delete(token_row)
        session.flush()
        return create_auth_session(session, user_row)
