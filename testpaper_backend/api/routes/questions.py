from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Query, Request, Response, status

from testpaper_backend.api.dependencies import QuestionsDeleteDep, QuestionsReadDep, QuestionsWriteDep, RateLimitWriteDep
from testpaper_backend.core.responses import envelope
from testpaper_backend.schemas import (
    Difficulty,
    Envelope,
    PaginatedResponse,
    QuestionCorrectionCreate,
    QuestionCorrectionEntity,
    QuestionCorrectionUpdate,
    QuestionCreate,
    QuestionEntity,
    QuestionRevisionEntity,
    QuestionType,
    QuestionUpdate,
    SortOrder,
)
from testpaper_backend.security import has_permission
from testpaper_backend.services.questions import (
    create_correction,
    create_question_for_user,
    delete_correction_entry,
    delete_question_for_user,
    delete_revision,
    ensure_question_correction_access,
    ensure_question_owner_access,
    get_question_or_404,
    list_corrections,
    list_revisions,
    query_questions_page,
    question_to_dict,
    update_correction_status,
    update_question_for_user,
)
from testpaper_backend.services.realtime import realtime

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/questions", tags=["questions"])


def _list_question_page(
    *,
    current_user,
    q: str | None,
    subjects: str | None,
    difficulty: Difficulty | None,
    question_type: QuestionType | None,
    tags: str | None,
    has_latex: bool | None,
    owner_id: int | None,
    include_answer: bool,
    page: int,
    page_size: int,
    sort_by: str | None,
    sort_order: SortOrder,
) -> dict[str, Any]:
    can_read_answers = has_permission(current_user, "answers:read")
    page_data = query_questions_page(
        q=q,
        subjects=subjects,
        difficulty=difficulty,
        question_type=question_type,
        tags=tags,
        has_latex_filter=has_latex,
        owner_id=owner_id,
        sort_by=sort_by,
        sort_order=sort_order,
        page=page,
        page_size=page_size,
        search_answers=can_read_answers,
    )
    page_data["items"] = [question_to_dict(item, include_answer=include_answer and can_read_answers) for item in page_data["items"]]
    return page_data


@router.get("", response_model=Envelope[PaginatedResponse[QuestionEntity]])
def list_questions(
    request: Request,
    current_user: QuestionsReadDep,
    q: str | None = None,
    subjects: str | None = None,
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
    page_data = _list_question_page(
        current_user=current_user,
        q=q,
        subjects=subjects,
        difficulty=difficulty,
        question_type=type,
        tags=tags,
        has_latex=hasLatex,
        owner_id=ownerId,
        include_answer=includeAnswer,
        page=page,
        page_size=pageSize,
        sort_by=sortBy,
        sort_order=sortOrder,
    )
    return envelope(page_data, request)


@router.get("/mine", response_model=Envelope[PaginatedResponse[QuestionEntity]])
def list_my_questions(
    request: Request,
    current_user: QuestionsReadDep,
    q: str | None = None,
    subjects: str | None = None,
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
    page_data = _list_question_page(
        current_user=current_user,
        q=q,
        subjects=subjects,
        difficulty=difficulty,
        question_type=type,
        tags=tags,
        has_latex=hasLatex,
        owner_id=current_user.id,
        include_answer=includeAnswer,
        page=page,
        page_size=pageSize,
        sort_by=sortBy,
        sort_order=sortOrder,
    )
    return envelope(page_data, request)


@router.get("/{question_public_id}", response_model=Envelope[QuestionEntity])
def get_question(
    request: Request,
    question_public_id: str,
    current_user: QuestionsReadDep,
    includeAnswer: bool = True,
):
    question = get_question_or_404(question_public_id)
    return envelope(question_to_dict(question, include_answer=includeAnswer and has_permission(current_user, "answers:read")), request)


@router.post("", response_model=Envelope[QuestionEntity], status_code=status.HTTP_201_CREATED)
def create_question(
    request: Request,
    background_tasks: BackgroundTasks,
    payload: QuestionCreate,
    current_user: QuestionsWriteDep,
    _: RateLimitWriteDep,
):
    question = create_question_for_user(payload, current_user)

    background_tasks.add_task(
        realtime.broadcast,
        "question.created",
        {"question": question_to_dict(question, include_answer=False), "actorId": current_user.id},
    )
    logger.info("Question created: %s by user %s", question.publicId, current_user.publicId)
    return envelope(question_to_dict(question), request)


@router.patch("/{question_public_id}", response_model=Envelope[QuestionEntity])
def update_question(
    request: Request,
    background_tasks: BackgroundTasks,
    question_public_id: str,
    payload: QuestionUpdate,
    current_user: QuestionsWriteDep,
    _: RateLimitWriteDep,
):
    updated = update_question_for_user(question_public_id, payload, current_user)

    background_tasks.add_task(
        realtime.broadcast,
        "question.updated",
        {"question": question_to_dict(updated, include_answer=False), "actorId": current_user.id},
    )
    logger.info("Question updated: %s", question_public_id)
    return envelope(question_to_dict(updated), request)


@router.delete("/{question_public_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_question(
    background_tasks: BackgroundTasks,
    question_public_id: str,
    current_user: QuestionsDeleteDep,
    _: RateLimitWriteDep,
):
    question = delete_question_for_user(question_public_id, current_user)

    background_tasks.add_task(
        realtime.broadcast,
        "question.deleted",
        {"questionId": question.publicId, "actorId": current_user.id},
    )
    logger.info("Question deleted: %s", question_public_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{question_public_id}/revisions", response_model=Envelope[list[QuestionRevisionEntity]])
def get_question_revisions(
    request: Request,
    question_public_id: str,
    current_user: QuestionsReadDep,
):
    question = get_question_or_404(question_public_id)
    revisions = list_revisions(question.id)
    return envelope([rev.model_dump(mode="json") for rev in revisions], request)


@router.post("/{question_public_id}/corrections", response_model=Envelope[QuestionCorrectionEntity], status_code=status.HTTP_201_CREATED)
def create_question_correction(
    request: Request,
    question_public_id: str,
    payload: QuestionCorrectionCreate,
    current_user: QuestionsReadDep,
    _: RateLimitWriteDep,
):
    question = get_question_or_404(question_public_id)
    correction = create_correction(question.id, current_user.id, payload)
    return envelope(correction.model_dump(mode="json"), request)


@router.get("/{question_public_id}/corrections", response_model=Envelope[list[QuestionCorrectionEntity]])
def get_question_corrections(
    request: Request,
    question_public_id: str,
    current_user: QuestionsReadDep,
):
    question = get_question_or_404(question_public_id)
    corrections = list_corrections(question.id)
    return envelope([c.model_dump(mode="json") for c in corrections], request)


@router.patch("/{question_public_id}/corrections/{correction_id}", response_model=Envelope[QuestionCorrectionEntity])
def update_question_correction(
    request: Request,
    question_public_id: str,
    correction_id: int,
    payload: QuestionCorrectionUpdate,
    current_user: QuestionsWriteDep,
    _: RateLimitWriteDep,
):
    question = get_question_or_404(question_public_id)
    ensure_question_correction_access(question, current_user)
    updated = update_correction_status(correction_id, question.id, payload.status)
    return envelope(updated.model_dump(mode="json"), request)


@router.delete("/{question_public_id}/revisions/{revision_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_question_revision(
    question_public_id: str,
    revision_id: int,
    current_user: QuestionsDeleteDep,
    _: RateLimitWriteDep,
):
    question = get_question_or_404(question_public_id)
    ensure_question_owner_access(question, current_user)
    delete_revision(revision_id, question.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{question_public_id}/corrections/{correction_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_question_correction(
    question_public_id: str,
    correction_id: int,
    current_user: QuestionsDeleteDep,
    _: RateLimitWriteDep,
):
    question = get_question_or_404(question_public_id)
    ensure_question_owner_access(question, current_user)
    delete_correction_entry(correction_id, question.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
