from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from testpaper_backend.config import (
    get_rate_limit_max_attempts,
    get_rate_limit_window_seconds,
    get_rate_limit_write_max_attempts,
    get_rate_limit_write_window_seconds,
)
from testpaper_backend.schemas import UserEntity
from testpaper_backend.security import get_current_user, require_permission
from testpaper_backend.services.rate_limit import check_rate_limit, get_client_ip

CurrentUserDep = Annotated[UserEntity, Depends(get_current_user)]
QuestionsReadDep = Annotated[UserEntity, Depends(require_permission("questions:read"))]
QuestionsWriteDep = Annotated[UserEntity, Depends(require_permission("questions:write"))]
QuestionsDeleteDep = Annotated[UserEntity, Depends(require_permission("questions:delete"))]
PapersReadDep = Annotated[UserEntity, Depends(require_permission("papers:read"))]
PapersWriteDep = Annotated[UserEntity, Depends(require_permission("papers:write"))]
UsersManageDep = Annotated[UserEntity, Depends(require_permission("users:manage"))]


def _rate_limit(request: Request, scope: str, max_attempts: int, window_seconds: int) -> None:
    ip = get_client_ip(request)
    check_rate_limit(f"{scope}:{ip}", max_attempts, window_seconds)


def _rate_limit_login(request: Request) -> None:
    _rate_limit(request, "login", get_rate_limit_max_attempts(), get_rate_limit_window_seconds())


def _rate_limit_register(request: Request) -> None:
    _rate_limit(request, "register", get_rate_limit_max_attempts(), get_rate_limit_window_seconds())


RateLimitLoginDep = Annotated[None, Depends(_rate_limit_login)]
RateLimitRegisterDep = Annotated[None, Depends(_rate_limit_register)]


def _rate_limit_write(request: Request) -> None:
    _rate_limit(request, "write", get_rate_limit_write_max_attempts(), get_rate_limit_write_window_seconds())


RateLimitWriteDep = Annotated[None, Depends(_rate_limit_write)]
