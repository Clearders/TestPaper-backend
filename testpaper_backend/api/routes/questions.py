from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from testpaper_backend.api.dependencies import QuestionsDeleteDep, QuestionsReadDep, QuestionsWriteDep
from testpaper_backend.core.responses import envelope
from testpaper_backend.repositories import QUESTIONS
from testpaper_backend.schemas import (
    Difficulty,
    Envelope,
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
    apply_question_update,
    create_correction,
    delete_correction_entry,
    delete_revision,
    ensure_question_owner_access,
    get_question_or_404,
    list_corrections,
    list_revisions,
    normalize_question_owner,
    normalize_question_payload,
    query_questions_page,
    question_to_dict,
    update_correction_status,
    validate_question_payload,
)
from testpaper_backend.services.realtime import realtime

router = APIRouter(prefix="/api/v1/questions", tags=["questions"])


@router.get("", response_model=Envelope[list[QuestionEntity]])
async def list_questions(
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
    can_read_answers = has_permission(current_user, "answers:read")
    page_data = query_questions_page(
        q=q,
        subjects=subjects,
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


@router.get("/mine", response_model=Envelope[list[QuestionEntity]])
async def list_my_questions(
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
    can_read_answers = has_permission(current_user, "answers:read")
    page_data = query_questions_page(
        q=q,
        subjects=subjects,
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


@router.get("/{question_public_id}", response_model=Envelope[QuestionEntity])
async def get_question(
    request: Request,
    question_public_id: str,
    current_user: QuestionsReadDep,
    includeAnswer: bool = True,
):
    question = get_question_or_404(question_public_id)
    return envelope(question_to_dict(question, include_answer=includeAnswer and has_permission(current_user, "answers:read")), request)


@router.post("", response_model=Envelope[QuestionEntity], status_code=status.HTTP_201_CREATED)
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


@router.patch("/{question_public_id}", response_model=Envelope[QuestionEntity])
async def update_question(
    request: Request,
    question_public_id: str,
    payload: QuestionUpdate,
    current_user: QuestionsWriteDep,
):
    question = get_question_or_404(question_public_id)
    ensure_question_owner_access(question, current_user)
    if "ownerId" in payload.model_fields_set and payload.ownerId is None and not has_permission(current_user, "users:manage"):
        payload.ownerId = current_user.id
    elif payload.ownerId is not None:
        payload.ownerId = normalize_question_owner(payload.ownerId, current_user)
    updated = apply_question_update(question, payload, current_user.id)
    QUESTIONS[question.id] = updated
    await realtime.broadcast("question.updated", {"question": question_to_dict(updated, include_answer=False), "actorId": current_user.id})
    return envelope(question_to_dict(updated), request)


@router.delete("/{question_public_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_question(question_public_id: str, current_user: QuestionsDeleteDep):
    question = get_question_or_404(question_public_id)
    ensure_question_owner_access(question, current_user)
    del QUESTIONS[question.id]
    await realtime.broadcast("question.deleted", {"questionId": question.publicId, "actorId": current_user.id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{question_public_id}/revisions", response_model=Envelope[list[QuestionRevisionEntity]])
async def get_question_revisions(
    request: Request,
    question_public_id: str,
    current_user: QuestionsReadDep,
):
    get_question_or_404(question_public_id)
    revisions = list_revisions(question_public_id)
    return envelope([rev.model_dump(mode="json") for rev in revisions], request)


@router.post("/{question_public_id}/corrections", response_model=Envelope[QuestionCorrectionEntity], status_code=status.HTTP_201_CREATED)
async def create_question_correction(
    request: Request,
    question_public_id: str,
    payload: QuestionCorrectionCreate,
    current_user: QuestionsReadDep,
):
    get_question_or_404(question_public_id)
    correction = create_correction(question_public_id, current_user.id, payload)
    return envelope(correction.model_dump(mode="json"), request)


@router.get("/{question_public_id}/corrections", response_model=Envelope[list[QuestionCorrectionEntity]])
async def get_question_corrections(
    request: Request,
    question_public_id: str,
    current_user: QuestionsReadDep,
):
    get_question_or_404(question_public_id)
    corrections = list_corrections(question_public_id)
    return envelope([c.model_dump(mode="json") for c in corrections], request)


@router.patch("/{question_public_id}/corrections/{correction_id}", response_model=Envelope[QuestionCorrectionEntity])
async def update_question_correction(
    request: Request,
    question_public_id: str,
    correction_id: int,
    payload: QuestionCorrectionUpdate,
    current_user: QuestionsWriteDep,
):
    question = get_question_or_404(question_public_id)
    if question.ownerId not in (None, current_user.id) and not has_permission(current_user, "users:manage"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": "Only the question owner or an administrator can manage corrections"},
        )
    corrections = list_corrections(question_public_id)
    if not any(c.id == correction_id for c in corrections):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "CORRECTION_NOT_FOUND", "message": "Correction not found for this question"},
        )
    updated = update_correction_status(correction_id, payload.status)
    return envelope(updated.model_dump(mode="json"), request)


@router.delete("/{question_public_id}/revisions/{revision_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_question_revision(
    question_public_id: str,
    revision_id: int,
    current_user: QuestionsDeleteDep,
):
    delete_revision(revision_id, question_public_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{question_public_id}/corrections/{correction_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_question_correction(
    question_public_id: str,
    correction_id: int,
    current_user: QuestionsDeleteDep,
):
    delete_correction_entry(correction_id, question_public_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
