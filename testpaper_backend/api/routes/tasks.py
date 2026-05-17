from __future__ import annotations

from typing import Any

from celery.result import AsyncResult
from fastapi import APIRouter, Query, Request

from testpaper_backend.api.dependencies import PapersReadDep, QuestionsReadDep, UsersManageDep
from testpaper_backend.core.responses import envelope
from testpaper_backend.security import has_permission
from testpaper_backend.worker.celery_app import celery

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


@router.post("/ping")
async def task_ping(request: Request, current_user: QuestionsReadDep):
    """Dispatch a Celery ping task and return its task ID for polling."""
    result = celery.send_task("ping")
    return envelope({"taskId": result.id, "status": "dispatched"}, request)


@router.get("/{task_id}")
async def task_status(request: Request, task_id: str, current_user: QuestionsReadDep):
    """Poll the status/result of any Celery task by ID."""
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

    return envelope(response_data, request)


@router.post("/export-paper/{paper_id}")
async def task_export_paper(
    request: Request,
    paper_id: int,
    current_user: PapersReadDep,
    question_order: str = Query(default="paper", pattern="^(paper|categorized)$"),
    include_answer: bool = Query(default=True),
    export_format: str = Query(default="json", alias="format", pattern="^(json|csv|txt)$"),
):
    """Dispatch an asynchronous paper export. Returns a task ID for polling."""
    result = celery.send_task(
        "export_paper",
        args=[paper_id],
        kwargs={
            "question_order": question_order,
            "include_answer": include_answer and has_permission(current_user, "answers:read"),
            "format": export_format,
        },
    )
    return envelope(
        {"taskId": result.id, "status": "dispatched", "paperId": paper_id},
        request,
    )


@router.post("/validate-questions")
async def task_validate_all_questions(
    request: Request,
    current_user: QuestionsReadDep,
):
    """Dispatch an async validation of all questions."""
    result = celery.send_task("validate_all_questions")
    return envelope({"taskId": result.id, "status": "dispatched"}, request)


@router.post("/validate-question/{question_id}")
async def task_validate_question(
    request: Request,
    question_id: int,
    current_user: QuestionsReadDep,
):
    """Dispatch an async validation of a single question."""
    result = celery.send_task("validate_question", args=[question_id])
    return envelope({"taskId": result.id, "status": "dispatched", "questionId": question_id}, request)


@router.post("/cleanup-expired-sessions")
async def task_cleanup_expired_sessions(
    request: Request,
    current_user: UsersManageDep,
):
    """Dispatch an async cleanup of expired auth tokens."""
    result = celery.send_task("cleanup_expired_sessions")
    return envelope({"taskId": result.id, "status": "dispatched"}, request)


@router.get("/stats/questions")
async def task_compute_question_stats(
    request: Request,
    current_user: QuestionsReadDep,
):
    """Dispatch async question statistics computation."""
    result = celery.send_task("compute_question_stats")
    return envelope({"taskId": result.id, "status": "dispatched"}, request)
