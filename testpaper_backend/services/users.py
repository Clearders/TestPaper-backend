from __future__ import annotations

from typing import cast

from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from testpaper_backend.db import AuthTokenRow, SessionLocal, UserRow
from testpaper_backend.schemas import UserCreate, UserEntity, UserRole, UserUpdate
from testpaper_backend.security import password_hash, user_row_to_entity
from testpaper_backend.time_utils import now_utc


def _user_not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "USER_NOT_FOUND", "message": "User not found"})


def _username_exists() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": "USER_ALREADY_EXISTS", "message": "Username already exists"},
    )


def _self_modification_forbidden(message: str) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={"code": "SELF_MODIFICATION_FORBIDDEN", "message": message},
    )


def list_user_accounts() -> list[UserEntity]:
    with SessionLocal() as session:
        rows = session.scalars(select(UserRow).order_by(UserRow.id)).all()
        return [user_row_to_entity(row) for row in rows]


def create_user_account(payload: UserCreate) -> UserEntity:
    with SessionLocal() as session:
        existing = session.scalars(select(UserRow).where(UserRow.username == payload.username)).first()
        if existing is not None:
            raise _username_exists()

        now = now_utc()
        user_row = UserRow(
            username=payload.username,
            display_name=payload.displayName,
            password_hash=password_hash(payload.password),
            role=payload.role.value,
            is_active=payload.isActive,
            created_at=now,
            updated_at=now,
        )
        session.add(user_row)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise _username_exists() from exc
        session.refresh(user_row)
        return user_row_to_entity(user_row)


def update_managed_user(user_public_id: str, payload: UserUpdate, current_user: UserEntity) -> UserEntity:
    with SessionLocal() as session:
        user_row = cast(UserRow | None, session.scalars(select(UserRow).where(UserRow.public_id == user_public_id)).first())
        if user_row is None:
            raise _user_not_found()

        patch = payload.model_dump(exclude_unset=True)
        if current_user.id == user_row.id:
            if "role" in patch and patch["role"] is not None:
                raise _self_modification_forbidden("Cannot modify your own role")
            if "isActive" in patch and not patch["isActive"]:
                raise _self_modification_forbidden("Cannot deactivate your own account")
        if "displayName" in patch:
            user_row.display_name = patch["displayName"]
        if "password" in patch:
            user_row.password_hash = password_hash(patch["password"])
            session.execute(delete(AuthTokenRow).where(AuthTokenRow.user_id == user_row.id))
        if "role" in patch and patch["role"] is not None:
            user_row.role = patch["role"].value if isinstance(patch["role"], UserRole) else str(patch["role"])
        if "isActive" in patch:
            user_row.is_active = bool(patch["isActive"])
        user_row.updated_at = now_utc()
        session.commit()
        session.refresh(user_row)
        return user_row_to_entity(user_row)


def delete_managed_user(user_public_id: str, current_user: UserEntity) -> None:
    if current_user.publicId == user_public_id:
        raise HTTPException(
            status_code=422,
            detail={"code": "VALIDATION_ERROR", "message": "You cannot delete your own account"},
        )
    with SessionLocal() as session:
        user_row = cast(UserRow | None, session.scalars(select(UserRow).where(UserRow.public_id == user_public_id)).first())
        if user_row is None:
            raise _user_not_found()
        session.delete(user_row)
        session.commit()
