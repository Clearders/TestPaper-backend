from __future__ import annotations

from typing import cast

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import select

from testpaper_backend.api.dependencies import UsersManageDep
from testpaper_backend.core.responses import envelope
from testpaper_backend.db import SessionLocal, UserRow
from testpaper_backend.schemas import Envelope, UserCreate, UserEntity, UserRole, UserUpdate
from testpaper_backend.security import password_hash, user_row_to_entity
from testpaper_backend.time_utils import now_utc

router = APIRouter(prefix="/api/v1/users", tags=["users"])


@router.get("", response_model=Envelope[list[UserEntity]])
async def list_users(request: Request, current_user: UsersManageDep):
    with SessionLocal() as session:
        rows = session.scalars(select(UserRow).order_by(UserRow.id)).all()
        return envelope([user_row_to_entity(row).model_dump(mode="json") for row in rows], request)


@router.post("", response_model=Envelope[UserEntity], status_code=status.HTTP_201_CREATED)
async def create_user(request: Request, payload: UserCreate, current_user: UsersManageDep):
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
        session.commit()
        session.refresh(user_row)
        return envelope(user_row_to_entity(user_row).model_dump(mode="json"), request)


@router.patch("/{user_public_id}", response_model=Envelope[UserEntity])
async def update_user(request: Request, user_public_id: str, payload: UserUpdate, current_user: UsersManageDep):
    with SessionLocal() as session:
        user_row = cast(UserRow | None, session.scalars(select(UserRow).where(UserRow.public_id == user_public_id)).first())
        if user_row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "USER_NOT_FOUND", "message": "User not found"})

        patch = payload.model_dump(exclude_unset=True)
        if "displayName" in patch:
            user_row.display_name = patch["displayName"]
        if "password" in patch:
            user_row.password_hash = password_hash(patch["password"])
        if "role" in patch and patch["role"] is not None:
            user_row.role = patch["role"].value if isinstance(patch["role"], UserRole) else str(patch["role"])
        if "isActive" in patch:
            user_row.is_active = bool(patch["isActive"])
        user_row.updated_at = now_utc()
        session.commit()
        session.refresh(user_row)
        return envelope(user_row_to_entity(user_row).model_dump(mode="json"), request)


@router.delete("/{user_public_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_public_id: str, current_user: UsersManageDep):
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
    return Response(status_code=status.HTTP_204_NO_CONTENT)
