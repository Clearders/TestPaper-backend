from __future__ import annotations

import logging
from collections.abc import Sequence
from enum import StrEnum
from typing import Any
from uuid import uuid4

from celery.result import AsyncResult
from fastapi import HTTPException, status

from testpaper_backend.redis_client import get_redis
from testpaper_backend.schemas import UserEntity
from testpaper_backend.security import has_permission
from testpaper_backend.worker.celery_app import celery

logger = logging.getLogger(__name__)

TASK_OWNER_PREFIX = "task-owner:"
TASK_OWNER_TTL_SECONDS = 3700
TASK_DISPATCHED_STATUS = "dispatched"


class TaskName(StrEnum):
    PING = "ping"
    EXPORT_PAPER = "export_paper"
    VALIDATE_ALL_QUESTIONS = "validate_all_questions"
    VALIDATE_QUESTION = "validate_question"
    CLEANUP_EXPIRED_SESSIONS = "cleanup_expired_sessions"
    COMPUTE_QUESTION_STATS = "compute_question_stats"


def _owner_key(task_id: str) -> str:
    return f"{TASK_OWNER_PREFIX}{task_id}"


def dispatch_owned_task(
    name: str | TaskName,
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
        return celery.send_task(str(name), args=list(args or ()), kwargs=kwargs or {}, task_id=task_id)
    except Exception as exc:
        try:
            client.delete(_owner_key(task_id))
        except Exception:
            logger.debug("Failed to clean up owner key for task %s after dispatch failure.", task_id, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "TASK_QUEUE_UNAVAILABLE", "message": "Task queue is temporarily unavailable"},
        ) from exc


def dispatched_task_payload(result: AsyncResult, **extra: Any) -> dict[str, Any]:
    return {"taskId": result.id, "status": TASK_DISPATCHED_STATUS, **extra}


def task_status_payload(task_id: str) -> dict[str, Any]:
    result = AsyncResult(task_id, app=celery)
    response_data: dict[str, Any] = {
        "taskId": task_id,
        "status": result.state,
    }
    if result.state == "SUCCESS":
        response_data["result"] = result.result
    elif result.state == "FAILURE":
        response_data["error"] = str(result.info) if result.info else "Unknown error"
    elif result.state == "PROGRESS":
        response_data["progress"] = result.info if result.info else None
    elif result.state == "REVOKED":
        response_data["message"] = "Task was revoked"
    return response_data


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
