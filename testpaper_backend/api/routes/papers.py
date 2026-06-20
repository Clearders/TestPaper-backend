from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, Response, status

from testpaper_backend.api.dependencies import PapersReadDep, PapersWriteDep, RateLimitWriteDep
from testpaper_backend.core.responses import envelope
from testpaper_backend.documents.paper_docx import DOCX_MEDIA_TYPE, build_paper_docx, docx_filename, resolve_layout_density
from testpaper_backend.schemas import (
    Envelope,
    ExportPreviewRequest,
    LayoutDensity,
    PaperCreate,
    PaperEntity,
    PaperGenerateRequest,
    PaperUpdate,
    QuestionOrder,
    QuestionOrderUpdate,
    QuestionRef,
)
from testpaper_backend.security import has_permission
from testpaper_backend.services.paper_create import create_paper_from_payload, generate_paper_from_result
from testpaper_backend.services.paper_generation import generate_paper_with_genetic_algorithm
from testpaper_backend.services.papers import (
    add_questions_to_paper,
    build_export_questions,
    ensure_paper_owner_access,
    get_paper_or_404,
    paper_with_questions,
    remove_question_from_paper,
    reorder_paper_question_refs,
    update_paper_metadata,
    validate_unique_question_refs,
)
from testpaper_backend.services.realtime import realtime

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/papers", tags=["papers"])


@router.post("", response_model=Envelope[PaperEntity], status_code=status.HTTP_201_CREATED)
def create_paper(
    request: Request,
    background_tasks: BackgroundTasks,
    payload: PaperCreate,
    current_user: PapersWriteDep,
    _: RateLimitWriteDep,
):
    validate_unique_question_refs(payload.questions, "questions")
    paper = create_paper_from_payload(payload, owner_id=current_user.id)
    background_tasks.add_task(
        realtime.broadcast,
        "paper.created",
        {"paper": paper.model_dump(mode="json"), "actorId": current_user.id},
    )
    logger.info("Paper created: %s", paper.publicId)
    return envelope(paper_with_questions(paper), request)


@router.post("/generate", status_code=status.HTTP_201_CREATED)
def generate_paper(
    request: Request,
    background_tasks: BackgroundTasks,
    payload: PaperGenerateRequest,
    current_user: PapersWriteDep,
    _: RateLimitWriteDep,
):
    candidate_owner = current_user.id if payload.ownQuestionsOnly else None
    generated = generate_paper_with_genetic_algorithm(payload, owner_id=candidate_owner)
    if not generated["paperQuestions"]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "VALIDATION_ERROR", "message": "Paper must contain at least one question"},
        )
    paper = generate_paper_from_result(payload, generated, owner_id=current_user.id)
    paper_payload = paper_with_questions(paper)
    background_tasks.add_task(
        realtime.broadcast,
        "paper.created",
        {"paper": paper.model_dump(mode="json"), "actorId": current_user.id},
    )
    logger.info("Paper generated: %s", paper.publicId)
    return envelope(
        {
            "paper": paper_payload,
            "diagnostics": generated["diagnostics"],
        },
        request,
    )


@router.get("/{paper_public_id}", response_model=Envelope[PaperEntity])
def get_paper(
    request: Request,
    paper_public_id: str,
    current_user: PapersReadDep,
    expand: str | None = None,
    includeAnswer: bool = True,
):
    paper = get_paper_or_404(paper_public_id)
    if expand == "questions":
        return envelope(paper_with_questions(paper, include_answer=includeAnswer and has_permission(current_user, "answers:read")), request)
    return envelope(paper.model_dump(mode="json"), request)


@router.patch("/{paper_public_id}", response_model=Envelope[PaperEntity])
def update_paper(
    request: Request,
    background_tasks: BackgroundTasks,
    paper_public_id: str,
    payload: PaperUpdate,
    current_user: PapersWriteDep,
    _: RateLimitWriteDep,
):
    paper = get_paper_or_404(paper_public_id)
    ensure_paper_owner_access(paper, current_user)
    updated = update_paper_metadata(paper, payload)
    background_tasks.add_task(
        realtime.broadcast,
        "paper.updated",
        {"paper": updated.model_dump(mode="json"), "actorId": current_user.id},
    )
    return envelope(updated.model_dump(mode="json"), request)


@router.post("/{paper_public_id}/questions", response_model=Envelope[PaperEntity])
def add_paper_questions(
    request: Request,
    background_tasks: BackgroundTasks,
    paper_public_id: str,
    payload: list[QuestionRef],
    current_user: PapersWriteDep,
    _: RateLimitWriteDep,
):
    paper = get_paper_or_404(paper_public_id)
    ensure_paper_owner_access(paper, current_user)
    updated = add_questions_to_paper(paper, payload)
    event_payload = {"paper": updated.model_dump(mode="json"), "actorId": current_user.id, "paperId": updated.publicId}
    background_tasks.add_task(realtime.broadcast, "paper.questions.added", event_payload)
    return envelope(paper_with_questions(updated), request)


@router.delete("/{paper_public_id}/questions/{question_public_id}", response_model=Envelope[PaperEntity])
def remove_paper_question(
    request: Request,
    background_tasks: BackgroundTasks,
    paper_public_id: str,
    question_public_id: str,
    current_user: PapersWriteDep,
    _: RateLimitWriteDep,
):
    paper = get_paper_or_404(paper_public_id)
    ensure_paper_owner_access(paper, current_user)
    updated = remove_question_from_paper(paper, question_public_id)
    background_tasks.add_task(
        realtime.broadcast,
        "paper.question.removed",
        {
            "paper": updated.model_dump(mode="json"),
            "questionId": question_public_id,
            "actorId": current_user.id,
            "paperId": updated.publicId,
        },
    )
    return envelope(paper_with_questions(updated), request)


@router.put("/{paper_public_id}/questions/order", response_model=Envelope[PaperEntity])
def reorder_paper_questions(
    request: Request,
    background_tasks: BackgroundTasks,
    paper_public_id: str,
    payload: QuestionOrderUpdate,
    current_user: PapersWriteDep,
    _: RateLimitWriteDep,
):
    paper = get_paper_or_404(paper_public_id)
    ensure_paper_owner_access(paper, current_user)
    updated = reorder_paper_question_refs(paper, payload)
    event_payload = {"paper": updated.model_dump(mode="json"), "actorId": current_user.id, "paperId": updated.publicId}
    background_tasks.add_task(realtime.broadcast, "paper.questions.reordered", event_payload)
    return envelope(paper_with_questions(updated), request)


@router.post("/{paper_public_id}/export-preview", response_model=Envelope[dict])
def export_preview(
    request: Request,
    paper_public_id: str,
    payload: ExportPreviewRequest,
    current_user: PapersReadDep,
):
    paper = get_paper_or_404(paper_public_id)
    questions = build_export_questions(paper, payload.questionOrder, payload.includeAnswer and has_permission(current_user, "answers:read"))
    return envelope(
        {
            "paper": paper.model_dump(mode="json"),
            "questions": questions,
            "renderHint": payload.model_dump(),
        },
        request,
    )


@router.get("/{paper_public_id}/download")
def download_paper(
    paper_public_id: str,
    current_user: PapersReadDep,
    format: str = Query(default="docx", pattern="^docx$"),
    questionOrder: QuestionOrder = QuestionOrder.paper,
    includeAnswer: bool = True,
    layoutDensity: LayoutDensity = LayoutDensity.auto,
):
    paper = get_paper_or_404(paper_public_id)
    include_answer = includeAnswer and has_permission(current_user, "answers:read")
    questions = build_export_questions(paper, questionOrder, include_answer)
    effective_layout_density = resolve_layout_density(questions, layoutDensity)
    file_bytes = build_paper_docx(paper, questions, include_answer=include_answer, layout_density=layoutDensity)
    filename = docx_filename(paper.title)
    ascii_filename = docx_filename(paper.title.encode("ascii", "ignore").decode("ascii") or "examination-paper")

    return Response(
        content=file_bytes,
        media_type=DOCX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f"attachment; filename=\"{ascii_filename}\"; filename*=UTF-8''{quote(filename)}",
            "X-Export-Format": format,
            "X-Layout-Density": effective_layout_density,
        },
    )
