from __future__ import annotations

import logging
from typing import cast

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from testpaper_backend.api.dependencies import RateLimitWriteDep, UsersManageDep
from testpaper_backend.core.responses import envelope
from testpaper_backend.db import AuthTokenRow, SessionLocal, UserRow
from testpaper_backend.schemas import Envelope, UserCreate, UserEntity, UserRole, UserUpdate
from testpaper_backend.security import password_hash, user_row_to_entity
from testpaper_backend.time_utils import now_utc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/users", tags=["users"])


@router.get("", response_model=Envelope[list[UserEntity]])
def list_users(request: Request, current_user: UsersManageDep):
    with SessionLocal() as session:
        rows = session.scalars(select(UserRow).order_by(UserRow.id)).all()
        return envelope([user_row_to_entity(row).model_dump(mode="json") for row in rows], request)


@router.post("", response_model=Envelope[UserEntity], status_code=status.HTTP_201_CREATED)
def create_user(request: Request, payload: UserCreate, current_user: UsersManageDep, _: RateLimitWriteDep):
    with SessionLocal() as session:
        existing = session.scalars(select(UserRow).where(UserRow.username == payload.username)).first()
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "USER_ALREADY_EXISTS", "message": "Username already exists"},
            )

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
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "USER_ALREADY_EXISTS", "message": "Username already exists"},
            ) from exc
        session.refresh(user_row)
        logger.info("User created: %s", user_row.public_id)
        return envelope(user_row_to_entity(user_row).model_dump(mode="json"), request)


@router.patch("/{user_public_id}", response_model=Envelope[UserEntity])
def update_user(request: Request, user_public_id: str, payload: UserUpdate, current_user: UsersManageDep, _: RateLimitWriteDep):
    with SessionLocal() as session:
        user_row = cast(UserRow | None, session.scalars(select(UserRow).where(UserRow.public_id == user_public_id)).first())
        if user_row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "USER_NOT_FOUND", "message": "User not found"})

        patch = payload.model_dump(exclude_unset=True)
        if current_user.id == user_row.id:
            if "role" in patch and patch["role"] is not None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={"code": "SELF_MODIFICATION_FORBIDDEN", "message": "Cannot modify your own role"},
                )
            if "isActive" in patch and not patch["isActive"]:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={"code": "SELF_MODIFICATION_FORBIDDEN", "message": "Cannot deactivate your own account"},
                )
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
        logger.info("User updated: %s", user_public_id)
        return envelope(user_row_to_entity(user_row).model_dump(mode="json"), request)


@router.delete("/{user_public_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_public_id: str, current_user: UsersManageDep, _: RateLimitWriteDep):
    if current_user.publicId == user_public_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "VALIDATION_ERROR", "message": "You cannot delete your own account"},
        )
    with SessionLocal() as session:
        user_row = cast(UserRow | None, session.scalars(select(UserRow).where(UserRow.public_id == user_public_id)).first())
        if user_row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "USER_NOT_FOUND", "message": "User not found"})
        session.delete(user_row)
        session.commit()
        logger.info("User deleted: %s", user_public_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
