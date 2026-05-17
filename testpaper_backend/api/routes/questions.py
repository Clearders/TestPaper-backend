from __future__ import annotations

from fastapi import APIRouter, Query, Request, Response, status

from testpaper_backend.api.dependencies import QuestionsDeleteDep, QuestionsReadDep, QuestionsWriteDep
from testpaper_backend.core.responses import envelope
from testpaper_backend.repositories import QUESTIONS
from testpaper_backend.schemas import Difficulty, QuestionCreate, QuestionType, QuestionUpdate, SortOrder
from testpaper_backend.security import has_permission
from testpaper_backend.services.questions import (
    apply_question_update,
    ensure_question_owner_access,
    get_question_or_404,
    normalize_question_owner,
    normalize_question_payload,
    query_questions_page,
    question_to_dict,
    validate_question_payload,
)
from testpaper_backend.services.realtime import realtime

router = APIRouter(prefix="/api/v1/questions", tags=["questions"])


@router.get("")
async def list_questions(
    request: Request,
    current_user: QuestionsReadDep,
    q: str | None = None,
    subject: str | None = None,
    difficulty: Difficulty | None = None,
    type: QuestionType | None = None,
    tags: str | None = None,
    hasLatex: bool | None = None,
    ownerId: int | None = None,
    includeAnswer: bool = True,
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=20, ge=1, le=100),
    sortBy: str | None = None,
    sortOrder: SortOrder = SortOrder.desc,
):
    can_read_answers = has_permission(current_user, "answers:read")
    page_data = query_questions_page(
        q=q,
        subject=subject,
        difficulty=difficulty,
        question_type=type,
        tags=tags,
        has_latex_filter=hasLatex,
        owner_id=ownerId,
        sort_by=sortBy,
        sort_order=sortOrder,
        page=page,
        page_size=pageSize,
        search_answers=can_read_answers,
    )
    page_data["items"] = [question_to_dict(item, include_answer=includeAnswer and can_read_answers) for item in page_data["items"]]
    return envelope(page_data, request)


@router.get("/mine")
async def list_my_questions(
    request: Request,
    current_user: QuestionsReadDep,
    q: str | None = None,
    subject: str | None = None,
    difficulty: Difficulty | None = None,
    type: QuestionType | None = None,
    tags: str | None = None,
    hasLatex: bool | None = None,
    includeAnswer: bool = True,
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=20, ge=1, le=100),
    sortBy: str | None = None,
    sortOrder: SortOrder = SortOrder.desc,
):
    can_read_answers = has_permission(current_user, "answers:read")
    page_data = query_questions_page(
        q=q,
        subject=subject,
        difficulty=difficulty,
        question_type=type,
        tags=tags,
        has_latex_filter=hasLatex,
        owner_id=current_user.id,
        sort_by=sortBy,
        sort_order=sortOrder,
        page=page,
        page_size=pageSize,
        search_answers=can_read_answers,
    )
    page_data["items"] = [question_to_dict(item, include_answer=includeAnswer and can_read_answers) for item in page_data["items"]]
    return envelope(page_data, request)


@router.get("/{question_id}")
async def get_question(
    request: Request,
    question_id: int,
    current_user: QuestionsReadDep,
    includeAnswer: bool = True,
):
    question = get_question_or_404(question_id)
    return envelope(question_to_dict(question, include_answer=includeAnswer and has_permission(current_user, "answers:read")), request)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_question(
    request: Request,
    payload: QuestionCreate,
    current_user: QuestionsWriteDep,
):
    validate_question_payload(payload)
    payload.ownerId = normalize_question_owner(payload.ownerId, current_user)
    question = QUESTIONS.create(normalize_question_payload(payload, question_id=0))
    await realtime.broadcast("question.created", {"question": question_to_dict(question, include_answer=False), "actorId": current_user.id})
    return envelope(question_to_dict(question), request)


@router.patch("/{question_id}")
async def update_question(
    request: Request,
    question_id: int,
    payload: QuestionUpdate,
    current_user: QuestionsWriteDep,
):
    question = get_question_or_404(question_id)
    ensure_question_owner_access(question, current_user)
    if "ownerId" in payload.model_fields_set and payload.ownerId is None and not has_permission(current_user, "users:manage"):
        payload.ownerId = current_user.id
    elif payload.ownerId is not None:
        payload.ownerId = normalize_question_owner(payload.ownerId, current_user)
    updated = apply_question_update(question, payload)
    QUESTIONS[question_id] = updated
    await realtime.broadcast("question.updated", {"question": question_to_dict(updated, include_answer=False), "actorId": current_user.id})
    return envelope(question_to_dict(updated), request)


@router.delete("/{question_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_question(question_id: int, current_user: QuestionsDeleteDep):
    question = get_question_or_404(question_id)
    ensure_question_owner_access(question, current_user)
    del QUESTIONS[question_id]
    await realtime.broadcast("question.deleted", {"questionId": question_id, "actorId": current_user.id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)
