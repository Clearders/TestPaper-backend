from __future__ import annotations

from datetime import timedelta

from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from testpaper_backend.config import get_avatar_upload_dir
from testpaper_backend.db import AuthTokenRow, SessionLocal, UserRow
from testpaper_backend.schemas import ImageUploadPayload, ImageUploadResponse, PasswordChange, ProfileUpdate, UserEntity
from testpaper_backend.security import password_hash, user_row_to_entity, verify_password
from testpaper_backend.services.png_uploads import PngUploadTarget, store_png_upload
from testpaper_backend.services.user_errors import user_not_found, username_exists
from testpaper_backend.time_utils import now_utc

MAX_AVATAR_BYTES = 500 * 1024
AVATAR_UPLOAD_DIR = get_avatar_upload_dir()
AVATAR_UPLOAD = PngUploadTarget(
    directory=AVATAR_UPLOAD_DIR,
    public_path="/api/v1/avatars",
    max_bytes=MAX_AVATAR_BYTES,
    too_large_message="Avatar image must be 500KB or smaller",
)


def store_avatar(payload: ImageUploadPayload, user_public_id: str) -> ImageUploadResponse:
    return store_png_upload(payload, AVATAR_UPLOAD, user_public_id)


def update_user_profile(user_id: int, payload: ProfileUpdate) -> UserEntity:
    with SessionLocal() as session:
        user_row = session.get(UserRow, user_id)
        if user_row is None:
            raise user_not_found()

        if payload.username is not None and payload.username != user_row.username:
            if user_row.last_username_changed_at is not None:
                days_since = now_utc() - user_row.last_username_changed_at
                if days_since < timedelta(days=30):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail={"code": "USERNAME_CHANGE_TOO_SOON", "message": "Username can only be changed once every 30 days"},
                    )
            existing = session.scalars(select(UserRow).where(UserRow.username == payload.username, UserRow.id != user_id)).first()
            if existing is not None:
                raise username_exists()
            user_row.username = payload.username
            user_row.last_username_changed_at = now_utc()

        if payload.displayName is not None:
            user_row.display_name = payload.displayName

        user_row.updated_at = now_utc()
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise username_exists() from exc
        session.refresh(user_row)
        return user_row_to_entity(user_row)


def change_user_password(user_id: int, payload: PasswordChange, current_token: str | None) -> None:
    with SessionLocal() as session:
        user_row = session.get(UserRow, user_id)
        if user_row is None:
            raise user_not_found()
        valid, _ = verify_password(payload.currentPassword, user_row.password_hash)
        if not valid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "INVALID_PASSWORD", "message": "Current password is incorrect"},
            )
        user_row.password_hash = password_hash(payload.newPassword)
        user_row.updated_at = now_utc()
        revoke_other_sessions = delete(AuthTokenRow).where(AuthTokenRow.user_id == user_id)
        if current_token:
            revoke_other_sessions = revoke_other_sessions.where(AuthTokenRow.token != current_token)
        session.execute(revoke_other_sessions)
        session.commit()


def update_user_avatar(user_id: int, user_public_id: str, payload: ImageUploadPayload) -> ImageUploadResponse:
    avatar = store_avatar(payload, user_public_id)
    with SessionLocal() as session:
        user_row = session.get(UserRow, user_id)
        if user_row is None:
            raise user_not_found()
        user_row.avatar_url = avatar.url
        user_row.updated_at = now_utc()
        session.commit()
        return avatar


def deactivate_user_account(user_id: int) -> None:
    with SessionLocal() as session:
        user_row = session.get(UserRow, user_id)
        if user_row is None:
            raise user_not_found()
        session.execute(delete(AuthTokenRow).where(AuthTokenRow.user_id == user_id))
        user_row.is_active = False
        user_row.updated_at = now_utc()
        session.commit()
