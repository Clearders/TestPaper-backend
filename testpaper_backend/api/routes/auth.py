from __future__ import annotations

from datetime import timedelta
from typing import cast

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import delete, select

from testpaper_backend.api.dependencies import CurrentUserDep, RateLimitLoginDep, RateLimitRegisterDep
from testpaper_backend.core.csrf import clear_csrf_cookie, generate_csrf_token, set_csrf_cookie
from testpaper_backend.core.responses import envelope
from testpaper_backend.db import AuthTokenRow, SessionLocal, UserRow
from testpaper_backend.schemas import (
    AuthSession,
    Envelope,
    ImageUploadPayload,
    ImageUploadResponse,
    LoginRequest,
    PasswordChange,
    ProfileUpdate,
    RegisterRequest,
    UserEntity,
    UserRole,
)
from testpaper_backend.security import (
    auth_error,
    get_request_token,
    password_hash,
    user_row_to_entity,
    verify_password,
)
from testpaper_backend.services.auth_sessions import (
    clear_auth_cookie,
    create_auth_session,
    refresh_auth_session,
    set_auth_cookie,
)
from testpaper_backend.services.profiles import store_avatar
from testpaper_backend.time_utils import now_utc

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/login", response_model=Envelope[AuthSession])
async def login(request: Request, response: Response, payload: LoginRequest, _: RateLimitLoginDep):
    username = payload.username.strip().lower()
    with SessionLocal() as session:
        user_row = cast(UserRow | None, session.scalars(select(UserRow).where(UserRow.username == username)).first())
        valid, needs_migration = verify_password(payload.password, user_row.password_hash)
        if user_row is None or not user_row.is_active or not valid:
            raise auth_error("INVALID_CREDENTIALS", "Invalid username or password")

        if needs_migration:
            user_row.password_hash = password_hash(payload.password)
            session.commit()

        token, auth_session = create_auth_session(session, user_row)
        set_auth_cookie(response, token, auth_session.expiresAt)
        set_csrf_cookie(response, generate_csrf_token())
        return envelope(auth_session.model_dump(mode="json"), request)


@router.post("/register", response_model=Envelope[AuthSession], status_code=status.HTTP_201_CREATED)
async def register(request: Request, response: Response, payload: RegisterRequest, _: RateLimitRegisterDep):
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
            role=UserRole.viewer.value,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        session.add(user_row)
        session.flush()

        token, auth_session = create_auth_session(session, user_row)
        set_auth_cookie(response, token, auth_session.expiresAt)
        set_csrf_cookie(response, generate_csrf_token())
        return envelope(auth_session.model_dump(mode="json"), request)


@router.get("/me", response_model=Envelope[UserEntity])
async def get_me(request: Request, current_user: CurrentUserDep):
    return envelope(current_user.model_dump(mode="json"), request)


@router.post("/refresh", response_model=Envelope[AuthSession])
async def refresh_session(request: Request, response: Response):
    token, auth_session = refresh_auth_session(get_request_token(request))
    set_auth_cookie(response, token, auth_session.expiresAt)
    return envelope(auth_session.model_dump(mode="json"), request)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request):
    token = get_request_token(request)
    with SessionLocal() as session:
        token_row = session.get(AuthTokenRow, token) if token else None
        if token_row is not None:
            session.delete(token_row)
            session.commit()
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_auth_cookie(response)
    clear_csrf_cookie(response)
    return response


@router.patch("/profile", response_model=Envelope[UserEntity])
async def update_profile(request: Request, payload: ProfileUpdate, current_user: CurrentUserDep):
    with SessionLocal() as session:
        user_row = cast(UserRow, session.get(UserRow, current_user.id))

        if payload.username is not None and payload.username != user_row.username:
            if user_row.last_username_changed_at is not None:
                days_since = now_utc() - user_row.last_username_changed_at
                if days_since < timedelta(days=30):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail={"code": "USERNAME_CHANGE_TOO_SOON", "message": "Username can only be changed once every 30 days"},
                    )
            existing = session.scalars(
                select(UserRow).where(UserRow.username == payload.username, UserRow.id != current_user.id)
            ).first()
            if existing is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"code": "USER_ALREADY_EXISTS", "message": "Username already exists"},
                )
            user_row.username = payload.username
            user_row.last_username_changed_at = now_utc()

        if payload.displayName is not None:
            user_row.display_name = payload.displayName

        user_row.updated_at = now_utc()
        session.commit()
        session.refresh(user_row)
        return envelope(user_row_to_entity(user_row).model_dump(mode="json"), request)


@router.put("/password")
async def change_password(payload: PasswordChange, current_user: CurrentUserDep):
    with SessionLocal() as session:
        user_row = cast(UserRow, session.get(UserRow, current_user.id))
        valid, _ = verify_password(payload.currentPassword, user_row.password_hash)
        if not valid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "INVALID_PASSWORD", "message": "Current password is incorrect"},
            )
        user_row.password_hash = password_hash(payload.newPassword)
        user_row.updated_at = now_utc()
        session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/avatar", response_model=Envelope[ImageUploadResponse])
async def upload_avatar(request: Request, payload: ImageUploadPayload, current_user: CurrentUserDep):
    avatar = store_avatar(payload, current_user.publicId)
    with SessionLocal() as session:
        user_row = cast(UserRow, session.get(UserRow, current_user.id))
        user_row.avatar_url = avatar.url
        user_row.updated_at = now_utc()
        session.commit()
        return envelope(avatar.model_dump(mode="json"), request)


@router.delete("/account", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(request: Request, current_user: CurrentUserDep):
    with SessionLocal() as session:
        session.execute(
            delete(AuthTokenRow).where(AuthTokenRow.user_id == current_user.id)
        )
        user_row = cast(UserRow, session.get(UserRow, current_user.id))
        user_row.is_active = False
        user_row.updated_at = now_utc()
        session.commit()
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_auth_cookie(response)
    clear_csrf_cookie(response)
    return response
