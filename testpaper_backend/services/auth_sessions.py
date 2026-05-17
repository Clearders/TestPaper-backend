from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import cast

from fastapi import Response
from sqlalchemy.orm import Session

from testpaper_backend.config import get_auth_cookie_domain, get_auth_cookie_name, get_auth_cookie_samesite, get_auth_cookie_secure
from testpaper_backend.db import AuthTokenRow, SessionLocal, UserRow
from testpaper_backend.schemas import AuthSession
from testpaper_backend.security import auth_error, user_row_to_entity
from testpaper_backend.time_utils import as_aware_utc, now_utc

SESSION_TTL = timedelta(hours=12)


def create_auth_session(session: Session, user_row: UserRow) -> tuple[str, AuthSession]:
    now = now_utc()
    session.query(AuthTokenRow).filter(AuthTokenRow.expires_at <= now).delete(synchronize_session=False)
    token = secrets.token_urlsafe(48)
    expires_at = now + SESSION_TTL
    session.add(AuthTokenRow(token=token, user_id=user_row.id, created_at=now, expires_at=expires_at))
    session.commit()
    session.refresh(user_row)
    return token, AuthSession(expiresAt=expires_at, user=user_row_to_entity(user_row))


def set_auth_cookie(response: Response, token: str, expires_at: datetime) -> None:
    max_age = max(0, int((expires_at - now_utc()).total_seconds()))
    response.set_cookie(
        key=get_auth_cookie_name(),
        value=token,
        max_age=max_age,
        expires=max_age,
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

        session.delete(token_row)
        session.flush()
        return create_auth_session(session, user_row)

