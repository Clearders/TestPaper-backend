from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast

from fastapi import HTTPException, Response, status
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from testpaper_backend.config import (
    get_access_token_ttl_minutes,
    get_auth_cookie_domain,
    get_auth_cookie_name,
    get_auth_cookie_samesite,
    get_auth_cookie_secure,
    get_refresh_token_ttl_days,
    get_session_ttl_hours,
)
from testpaper_backend.db import AuthAuditLogRow, AuthTokenRow, SessionLocal, UserRow
from testpaper_backend.schemas import (
    AuthSession,
    DeviceSessionEntity,
    LoginRequest,
    RegisterRequest,
    TokenPair,
    TokenType,
    UserRole,
)
from testpaper_backend.security import auth_error, password_hash, user_row_to_entity, verify_password
from testpaper_backend.services.user_errors import username_exists
from testpaper_backend.time_utils import as_aware_utc, now_utc

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeviceInfo:
    device_id: str
    device_name: str
    ip_address: str
    user_agent: str | None = None


def _session_ttl() -> timedelta:
    return timedelta(hours=get_session_ttl_hours())


def create_auth_session(session: Session, user_row: UserRow) -> tuple[str, AuthSession]:
    now = now_utc()
    session.query(AuthTokenRow).filter(AuthTokenRow.expires_at <= now).delete(synchronize_session=False)
    token = secrets.token_urlsafe(48)
    expires_at = now + _session_ttl()
    session.add(AuthTokenRow(token=token, user_id=user_row.id, created_at=now, expires_at=expires_at))
    session.commit()
    session.refresh(user_row)
    return token, AuthSession(expiresAt=expires_at, user=user_row_to_entity(user_row))


def _verify_credentials(session: Session, payload: LoginRequest) -> UserRow:
    username = payload.username.strip().lower()
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
    return user_row


def authenticate_user(payload: LoginRequest) -> tuple[str, AuthSession]:
    with SessionLocal() as session:
        user_row = _verify_credentials(session, payload)
        return create_auth_session(session, user_row)


def authenticate_native(payload: LoginRequest, device: DeviceInfo) -> TokenPair:
    with SessionLocal() as session:
        user_row = _verify_credentials(session, payload)
        token_pair = _issue_token_pair_for_user(session, user_row, device)
        log_audit_event(session, user_row.id, device.device_id, "login", device.ip_address)
        session.commit()
        return token_pair


def register_user(payload: RegisterRequest) -> tuple[str, AuthSession]:
    with SessionLocal() as session:
        existing = session.scalars(select(UserRow).where(UserRow.username == payload.username)).first()
        if existing is not None:
            raise username_exists()

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
            raise username_exists() from exc


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


def _create_token_row(
    session: Session,
    *,
    token_type: TokenType,
    user_id: int,
    ttl: timedelta,
    device: DeviceInfo | None,
    refresh_token_id: str | None = None,
) -> str:
    now = now_utc()
    token = secrets.token_urlsafe(48)
    session.add(
        AuthTokenRow(
            token=token,
            user_id=user_id,
            token_type=token_type.value,
            device_id=device.device_id if device else None,
            device_name=device.device_name if device else None,
            ip_address=device.ip_address if device else None,
            user_agent=device.user_agent if device else None,
            last_seen_at=now,
            refresh_token_id=refresh_token_id,
            created_at=now,
            expires_at=now + ttl,
        )
    )
    return token


def _issue_token_pair_for_user(session: Session, user_row: UserRow, device: DeviceInfo) -> TokenPair:
    """Issue a short-lived access token plus a long-lived refresh token."""
    access_ttl = timedelta(minutes=get_access_token_ttl_minutes())
    refresh_ttl = timedelta(days=get_refresh_token_ttl_days())
    refresh_token = _create_token_row(session, token_type=TokenType.refresh, user_id=user_row.id, ttl=refresh_ttl, device=device)
    access_token = _create_token_row(
        session,
        token_type=TokenType.access,
        user_id=user_row.id,
        ttl=access_ttl,
        device=device,
        refresh_token_id=refresh_token,
    )
    session.commit()
    session.refresh(user_row)
    return TokenPair(
        accessToken=access_token,
        refreshToken=refresh_token,
        expiresIn=int(access_ttl.total_seconds()),
        refreshExpiresIn=int(refresh_ttl.total_seconds()),
        user=user_row_to_entity(user_row),
    )


def refresh_token_pair(token: str, device: DeviceInfo | None = None) -> TokenPair:
    """Rotate a refresh token: revoke it (and its access token), then issue a fresh pair."""
    with SessionLocal() as session:
        token_row = cast(AuthTokenRow | None, session.get(AuthTokenRow, token))
        if token_row is None:
            raise auth_error("INVALID_TOKEN", "Invalid or expired token")
        if token_row.token_type != TokenType.refresh.value:
            raise auth_error("INVALID_TOKEN", "Expected a refresh token")
        if as_aware_utc(token_row.expires_at) <= now_utc():
            session.delete(token_row)
            session.commit()
            raise auth_error("TOKEN_EXPIRED", "Refresh token has expired")

        user_row = cast(UserRow | None, session.get(UserRow, token_row.user_id))
        if user_row is None or not user_row.is_active:
            session.delete(token_row)
            session.commit()
            raise auth_error("ACCOUNT_DISABLED", "Account is disabled")

        effective_device = device or DeviceInfo(
            device_id=token_row.device_id or "unknown-device",
            device_name=token_row.device_name or "Unknown device",
            ip_address=token_row.ip_address or "unknown",
            user_agent=token_row.user_agent,
        )

        # Revoke the old refresh token and any access token issued against it.
        session.execute(delete(AuthTokenRow).where(AuthTokenRow.token == token_row.token))
        session.execute(delete(AuthTokenRow).where(AuthTokenRow.refresh_token_id == token_row.token))
        session.flush()

        token_pair = _issue_token_pair_for_user(session, user_row, effective_device)
        log_audit_event(session, user_row.id, effective_device.device_id, "refresh", effective_device.ip_address)
        session.commit()
        return token_pair


def revoke_device(user_id: int, device_id: str, ip_address: str, current_token: str | None = None) -> None:
    with SessionLocal() as session:
        if current_token:
            token_row = cast(AuthTokenRow | None, session.get(AuthTokenRow, current_token))
            if token_row is not None and token_row.device_id == device_id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"code": "DEVICE_IS_CURRENT", "message": "Cannot revoke the current device"},
                )
        session.execute(delete(AuthTokenRow).where(AuthTokenRow.user_id == user_id, AuthTokenRow.device_id == device_id))
        log_audit_event(session, user_id, device_id, "device_revoked", ip_address)
        session.commit()


def list_devices(user_id: int, current_token: str | None) -> list[DeviceSessionEntity]:
    with SessionLocal() as session:
        rows = session.scalars(select(AuthTokenRow).where(AuthTokenRow.user_id == user_id, AuthTokenRow.device_id.is_not(None))).all()

    devices: dict[str, DeviceSessionEntity] = {}
    for row in rows:
        device_id = cast(str, row.device_id)
        existing = devices.get(device_id)
        if existing is None:
            devices[device_id] = DeviceSessionEntity(
                deviceId=device_id,
                deviceName=row.device_name or "Unknown device",
                lastSeenAt=row.last_seen_at,
                createdAt=row.created_at,
                current=row.token == current_token,
            )
            continue
        if existing.createdAt > row.created_at:
            existing.createdAt = row.created_at
        if row.last_seen_at is not None and (existing.lastSeenAt is None or existing.lastSeenAt < row.last_seen_at):
            existing.lastSeenAt = row.last_seen_at
        if row.token == current_token:
            existing.current = True
        if row.device_name:
            existing.deviceName = row.device_name
    return sorted(devices.values(), key=lambda device: device.createdAt)


def log_audit_event(session: Session, user_id: int, device_id: str | None, event: str, ip_address: str) -> None:
    session.add(
        AuthAuditLogRow(
            user_id=user_id,
            device_id=device_id,
            event=event,
            ip_address=ip_address,
            created_at=now_utc(),
        )
    )
