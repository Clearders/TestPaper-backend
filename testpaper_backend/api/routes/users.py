from __future__ import annotations

import logging

from fastapi import APIRouter, Request, Response, status

from testpaper_backend.api.dependencies import RateLimitWriteDep, UsersManageDep
from testpaper_backend.core.responses import envelope
from testpaper_backend.schemas import Envelope, UserCreate, UserEntity, UserUpdate
from testpaper_backend.services.users import create_user_account, delete_managed_user, list_user_accounts, update_managed_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/users", tags=["users"])


@router.get("", response_model=Envelope[list[UserEntity]])
def list_users(request: Request, current_user: UsersManageDep):
    users = list_user_accounts()
    return envelope([user.model_dump(mode="json") for user in users], request)


@router.post("", response_model=Envelope[UserEntity], status_code=status.HTTP_201_CREATED)
def create_user(request: Request, payload: UserCreate, current_user: UsersManageDep, _: RateLimitWriteDep):
    user = create_user_account(payload)
    logger.info("User created: %s", user.publicId)
    return envelope(user.model_dump(mode="json"), request)


@router.patch("/{user_public_id}", response_model=Envelope[UserEntity])
def update_user(request: Request, user_public_id: str, payload: UserUpdate, current_user: UsersManageDep, _: RateLimitWriteDep):
    user = update_managed_user(user_public_id, payload, current_user)
    logger.info("User updated: %s", user_public_id)
    return envelope(user.model_dump(mode="json"), request)


@router.delete("/{user_public_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_public_id: str, current_user: UsersManageDep, _: RateLimitWriteDep):
    delete_managed_user(user_public_id, current_user)
    logger.info("User deleted: %s", user_public_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
