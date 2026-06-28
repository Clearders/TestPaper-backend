from __future__ import annotations

import logging

from fastapi import APIRouter, Request, Response, status

from testpaper_backend.api.dependencies import CurrentUserDep, RateLimitLoginDep, RateLimitRegisterDep, RateLimitWriteDep
from testpaper_backend.core.csrf import clear_csrf_cookie, generate_csrf_token, set_csrf_cookie
from testpaper_backend.core.responses import envelope
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
)
from testpaper_backend.security import get_request_token
from testpaper_backend.services.auth_sessions import (
    authenticate_user,
    clear_auth_cookie,
    refresh_auth_session,
    register_user,
    revoke_auth_session,
    set_auth_cookie,
)
from testpaper_backend.services.profiles import change_user_password, deactivate_user_account, update_user_avatar, update_user_profile

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/login", response_model=Envelope[AuthSession])
def login(request: Request, response: Response, payload: LoginRequest, _: RateLimitLoginDep):
    token, auth_session = authenticate_user(payload)
    set_auth_cookie(response, token, auth_session.expiresAt)
    set_csrf_cookie(response, generate_csrf_token(), auth_session.expiresAt)
    return envelope(auth_session.model_dump(mode="json"), request)


@router.post("/register", response_model=Envelope[AuthSession], status_code=status.HTTP_201_CREATED)
def register(request: Request, response: Response, payload: RegisterRequest, _: RateLimitRegisterDep):
    token, auth_session = register_user(payload)
    set_auth_cookie(response, token, auth_session.expiresAt)
    set_csrf_cookie(response, generate_csrf_token(), auth_session.expiresAt)
    return envelope(auth_session.model_dump(mode="json"), request)


@router.get("/me", response_model=Envelope[UserEntity])
def get_me(request: Request, current_user: CurrentUserDep):
    return envelope(current_user.model_dump(mode="json"), request)


@router.post("/refresh", response_model=Envelope[AuthSession])
def refresh_session(request: Request, response: Response):
    token, auth_session = refresh_auth_session(get_request_token(request))
    set_auth_cookie(response, token, auth_session.expiresAt)
    set_csrf_cookie(response, generate_csrf_token(), auth_session.expiresAt)
    return envelope(auth_session.model_dump(mode="json"), request)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request):
    revoke_auth_session(get_request_token(request))
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_auth_cookie(response)
    clear_csrf_cookie(response)
    return response


@router.patch("/profile", response_model=Envelope[UserEntity])
def update_profile(request: Request, payload: ProfileUpdate, current_user: CurrentUserDep, _: RateLimitWriteDep):
    user = update_user_profile(current_user.id, payload)
    return envelope(user.model_dump(mode="json"), request)


@router.put("/password")
def change_password(request: Request, payload: PasswordChange, current_user: CurrentUserDep, _: RateLimitWriteDep):
    change_user_password(current_user.id, payload, get_request_token(request))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/avatar", response_model=Envelope[ImageUploadResponse])
def upload_avatar(request: Request, payload: ImageUploadPayload, current_user: CurrentUserDep, _: RateLimitWriteDep):
    avatar = update_user_avatar(current_user.id, current_user.publicId, payload)
    return envelope(avatar.model_dump(mode="json"), request)


@router.delete("/account", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(current_user: CurrentUserDep, _: RateLimitWriteDep):
    deactivate_user_account(current_user.id)
    logger.info("Account deleted: %s", current_user.publicId)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_auth_cookie(response)
    clear_csrf_cookie(response)
    return response
