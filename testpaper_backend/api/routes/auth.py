from __future__ import annotations

from typing import cast

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import select

from testpaper_backend.api.dependencies import CurrentUserDep
from testpaper_backend.core.csrf import clear_csrf_cookie, generate_csrf_token, set_csrf_cookie
from testpaper_backend.core.responses import envelope
from testpaper_backend.db import AuthTokenRow, SessionLocal, UserRow
from testpaper_backend.schemas import LoginRequest, RegisterRequest, UserRole
from testpaper_backend.security import auth_error, get_request_token, password_hash, verify_password
from testpaper_backend.services.auth_sessions import clear_auth_cookie, create_auth_session, refresh_auth_session, set_auth_cookie
from testpaper_backend.time_utils import now_utc

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/login")
async def login(request: Request, response: Response, payload: LoginRequest):
    username = payload.username.strip().lower()
    with SessionLocal() as session:
        user_row = cast(UserRow | None, session.scalars(select(UserRow).where(UserRow.username == username)).first())
        if user_row is None or not user_row.is_active or not verify_password(payload.password, user_row.password_hash):
            raise auth_error("INVALID_CREDENTIALS", "Invalid username or password")

        token, auth_session = create_auth_session(session, user_row)
        set_auth_cookie(response, token, auth_session.expiresAt)
        set_csrf_cookie(response, generate_csrf_token())
        return envelope(auth_session.model_dump(mode="json"), request)


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(request: Request, response: Response, payload: RegisterRequest):
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


@router.get("/me")
async def get_me(request: Request, current_user: CurrentUserDep):
    return envelope(current_user.model_dump(mode="json"), request)


@router.post("/refresh")
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
