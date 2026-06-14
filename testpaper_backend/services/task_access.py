from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import uuid4

from celery.result import AsyncResult
from fastapi import HTTPException, status

from testpaper_backend.redis_client import get_redis
from testpaper_backend.schemas import UserEntity
from testpaper_backend.security import has_permission
from testpaper_backend.worker.celery_app import celery

TASK_OWNER_PREFIX = "task-owner:"
TASK_OWNER_TTL_SECONDS = 3700


def _owner_key(task_id: str) -> str:
    return f"{TASK_OWNER_PREFIX}{task_id}"


def dispatch_owned_task(
    name: str,
    current_user: UserEntity,
    *,
    args: Sequence[Any] | None = None,
    kwargs: dict[str, Any] | None = None,
) -> AsyncResult:
    task_id = str(uuid4())
    try:
        client = get_redis()
        client.setex(_owner_key(task_id), TASK_OWNER_TTL_SECONDS, str(current_user.id))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "TASK_QUEUE_UNAVAILABLE", "message": "Task queue is temporarily unavailable"},
        ) from exc

    try:
        return celery.send_task(name, args=list(args or ()), kwargs=kwargs or {}, task_id=task_id)
    except Exception as exc:
        client.delete(_owner_key(task_id))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "TASK_QUEUE_UNAVAILABLE", "message": "Task queue is temporarily unavailable"},
        ) from exc


def ensure_task_access(task_id: str, current_user: UserEntity) -> None:
    if has_permission(current_user, "users:manage"):
        return

    try:
        owner_id = get_redis().get(_owner_key(task_id))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "TASK_ACCESS_UNAVAILABLE", "message": "Task access verification is temporarily unavailable"},
        ) from exc

    if owner_id is None or str(owner_id) != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "TASK_NOT_FOUND", "message": "Task not found"},
        )
