from __future__ import annotations

from fastapi import APIRouter, Query, Request

from testpaper_backend.api.dependencies import PapersReadDep, QuestionsReadDep, RateLimitWriteDep, UsersManageDep
from testpaper_backend.core.responses import envelope
from testpaper_backend.security import has_permission
from testpaper_backend.services.papers import get_paper_or_404
from testpaper_backend.services.questions import get_question_or_404
from testpaper_backend.services.task_access import (
    TaskName,
    dispatch_owned_task,
    dispatched_task_payload,
    ensure_task_access,
    task_status_payload,
)

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


@router.post("/ping")
def task_ping(request: Request, current_user: QuestionsReadDep, _: RateLimitWriteDep):
    """Dispatch a Celery ping task and return its task ID for polling."""
    result = dispatch_owned_task(TaskName.PING, current_user)
    return envelope(dispatched_task_payload(result), request)


@router.get("/{task_id}")
def task_status(request: Request, task_id: str, current_user: QuestionsReadDep):
    """Poll the status/result of any Celery task by ID."""
    ensure_task_access(task_id, current_user)
    return envelope(task_status_payload(task_id), request)


@router.post("/export-paper/{paper_public_id}")
def task_export_paper(
    request: Request,
    paper_public_id: str,
    current_user: PapersReadDep,
    _: RateLimitWriteDep,
    question_order: str = Query(default="paper", pattern="^(paper|categorized)$"),
    include_answer: bool = Query(default=True),
    export_format: str = Query(default="json", alias="format", pattern="^(json|csv|txt)$"),
):
    """Dispatch an asynchronous paper export. Returns a task ID for polling."""
    paper = get_paper_or_404(paper_public_id)
    result = dispatch_owned_task(
        TaskName.EXPORT_PAPER,
        current_user,
        args=[paper.id],
        kwargs={
            "question_order": question_order,
            "include_answer": include_answer and has_permission(current_user, "answers:read"),
            "format": export_format,
        },
    )
    return envelope(
        dispatched_task_payload(result, paperId=paper_public_id),
        request,
    )


@router.post("/validate-questions")
def task_validate_all_questions(
    request: Request,
    current_user: QuestionsReadDep,
    _: RateLimitWriteDep,
):
    """Dispatch an async validation of all questions."""
    result = dispatch_owned_task(TaskName.VALIDATE_ALL_QUESTIONS, current_user)
    return envelope(dispatched_task_payload(result), request)


@router.post("/validate-question/{question_public_id}")
def task_validate_question(
    request: Request,
    question_public_id: str,
    current_user: QuestionsReadDep,
    _: RateLimitWriteDep,
):
    """Dispatch an async validation of a single question."""
    question = get_question_or_404(question_public_id)
    result = dispatch_owned_task(TaskName.VALIDATE_QUESTION, current_user, args=[question.id])
    return envelope(dispatched_task_payload(result, questionId=question_public_id), request)


@router.post("/cleanup-expired-sessions")
def task_cleanup_expired_sessions(
    request: Request,
    current_user: UsersManageDep,
    _: RateLimitWriteDep,
):
    """Dispatch an async cleanup of expired auth tokens."""
    result = dispatch_owned_task(TaskName.CLEANUP_EXPIRED_SESSIONS, current_user)
    return envelope(dispatched_task_payload(result), request)


@router.post("/stats/questions")
def task_compute_question_stats(
    request: Request,
    current_user: QuestionsReadDep,
    _: RateLimitWriteDep,
):
    """Dispatch async question statistics computation."""
    result = dispatch_owned_task(TaskName.COMPUTE_QUESTION_STATS, current_user)
    return envelope(dispatched_task_payload(result), request)
