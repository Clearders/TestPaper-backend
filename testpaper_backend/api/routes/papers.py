from __future__ import annotations

from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from testpaper_backend.api.dependencies import PapersReadDep, PapersWriteDep
from testpaper_backend.core.responses import envelope
from testpaper_backend.documents.paper_docx import DOCX_MEDIA_TYPE, build_paper_docx, docx_filename
from testpaper_backend.repositories import PAPERS
from testpaper_backend.schemas import (
    ExportPreviewRequest,
    PaperCreate,
    PaperEntity,
    PaperGenerateRequest,
    QuestionRef,
    PaperStatus,
    PaperUpdate,
    QuestionOrder,
    QuestionOrderUpdate,
    QuestionRef,
)
from testpaper_backend.security import has_permission
from testpaper_backend.services.paper_generation import generate_paper_with_genetic_algorithm
from testpaper_backend.services.papers import (
    build_export_questions,
    get_paper_or_404,
    paper_with_questions,
    validate_unique_question_refs,
)
from testpaper_backend.services.questions import get_question_or_404
from testpaper_backend.services.realtime import realtime
from testpaper_backend.time_utils import now_utc

router = APIRouter(prefix="/api/v1/papers", tags=["papers"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_paper(request: Request, payload: PaperCreate, current_user: PapersWriteDep):
    validate_unique_question_refs(payload.questions, "questions")
    for item in payload.questions:
        get_question_or_404(item.questionId)
    paper = PaperEntity(
        id=0,
        publicId=str(uuid4()),
        title=payload.title,
        subject=payload.subject,
        duration=payload.duration,
        totalMarks=payload.totalMarks,
        questions=[QuestionRef(**item.model_dump()) for item in sorted(payload.questions, key=lambda item: item.orderNo)],
        status=PaperStatus.draft,
        createdAt=now_utc(),
        updatedAt=now_utc(),
    )
    paper = PAPERS.create(paper)
    await realtime.broadcast("paper.created", {"paper": paper.model_dump(mode="json"), "actorId": current_user.id})
    return envelope(paper_with_questions(paper), request)


@router.post("/generate", status_code=status.HTTP_201_CREATED)
async def generate_paper(request: Request, payload: PaperGenerateRequest, current_user: PapersWriteDep):
    owner_id = current_user.id if payload.ownQuestionsOnly else None
    generated = generate_paper_with_genetic_algorithm(payload, owner_id=owner_id)
    paper = PaperEntity(
        id=0,
        publicId=str(uuid4()),
        title=payload.title,
        subject=payload.subject,
        duration=payload.duration,
        totalMarks=payload.totalMarks,
        questions=generated["paperQuestions"],
        status=PaperStatus.draft,
        createdAt=now_utc(),
        updatedAt=now_utc(),
    )
    paper = PAPERS.create(paper)
    paper_payload = paper_with_questions(paper)
    await realtime.broadcast("paper.created", {"paper": paper.model_dump(mode="json"), "actorId": current_user.id})
    return envelope(
        {
            "paper": paper_payload,
            "diagnostics": generated["diagnostics"],
        },
        request,
    )


@router.get("/{paper_id}")
async def get_paper(
    request: Request,
    paper_id: int,
    current_user: PapersReadDep,
    expand: str | None = None,
    includeAnswer: bool = True,
):
    paper = get_paper_or_404(paper_id)
    if expand == "questions":
        return envelope(paper_with_questions(paper, include_answer=includeAnswer and has_permission(current_user, "answers:read")), request)
    return envelope(paper.model_dump(mode="json"), request)


@router.patch("/{paper_id}")
async def update_paper(
    request: Request,
    paper_id: int,
    payload: PaperUpdate,
    current_user: PapersWriteDep,
):
    paper = get_paper_or_404(paper_id)
    data = paper.model_dump()
    patch = payload.model_dump(exclude_unset=True)
    data.update(patch)
    data["updatedAt"] = now_utc()
    updated = PaperEntity(**data)
    PAPERS[paper_id] = updated
    await realtime.broadcast("paper.updated", {"paper": paper_to_dict(updated), "actorId": current_user.id})
    return envelope(paper_to_dict(updated), request)


@router.post("/{paper_id}/questions")
async def add_paper_questions(
    request: Request,
    paper_id: int,
    payload: list[QuestionRef],
    current_user: PapersWriteDep,
):
    paper = get_paper_or_404(paper_id)
    validate_unique_question_refs(payload, "questions")
    existing_ids = {item.questionId for item in paper.questions}
    existing_orders = {item.orderNo for item in paper.questions}
    additions = []
    for item in payload:
        get_question_or_404(item.questionId)
        if item.questionId in existing_ids:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "QUESTION_ALREADY_IN_PAPER", "message": "Question already exists in paper"},
            )
        if item.orderNo in existing_orders:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "VALIDATION_ERROR", "message": "orderNo already exists in paper"},
            )
        additions.append(QuestionRef(**item.model_dump()))
    paper.questions.extend(additions)
    paper.questions = sorted(paper.questions, key=lambda item: item.orderNo)
    paper.updatedAt = now_utc()
    PAPERS[paper_id] = paper
    await realtime.broadcast("paper.questions.added", {"paper": paper.model_dump(mode="json"), "actorId": current_user.id})
    return envelope(paper_with_questions(paper), request)


@router.delete("/{paper_id}/questions/{question_id}")
async def remove_paper_question(
    request: Request,
    paper_id: int,
    question_id: int,
    current_user: PapersWriteDep,
):
    paper = get_paper_or_404(paper_id)
    before = len(paper.questions)
    paper.questions = [item for item in paper.questions if item.questionId != question_id]
    if len(paper.questions) == before:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "QUESTION_NOT_FOUND", "message": "Question not found in paper"},
        )
    paper.updatedAt = now_utc()
    PAPERS[paper_id] = paper
    await realtime.broadcast(
        "paper.question.removed",
        {"paper": paper.model_dump(mode="json"), "questionId": question_id, "actorId": current_user.id},
    )
    return envelope(paper_with_questions(paper), request)


@router.put("/{paper_id}/questions/order")
async def reorder_paper_questions(
    request: Request,
    paper_id: int,
    payload: QuestionOrderUpdate,
    current_user: PapersWriteDep,
):
    paper = get_paper_or_404(paper_id)
    validate_unique_question_refs([QuestionRef(questionId=item.questionId, orderNo=item.orderNo) for item in payload.orders], "orders")
    order_map = {item.questionId: item.orderNo for item in payload.orders}
    existing_ids = {item.questionId for item in paper.questions}
    if set(order_map) != existing_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "VALIDATION_ERROR", "message": "orders must include every question in the paper"},
        )
    paper.questions = [
        QuestionRef(questionId=item.questionId, orderNo=order_map[item.questionId], marks=item.marks)
        for item in paper.questions
    ]
    paper.questions = sorted(paper.questions, key=lambda item: item.orderNo)
    paper.updatedAt = now_utc()
    PAPERS[paper_id] = paper
    await realtime.broadcast("paper.questions.reordered", {"paper": paper.model_dump(mode="json"), "actorId": current_user.id})
    return envelope(paper_with_questions(paper), request)


@router.post("/{paper_id}/export-preview")
async def export_preview(
    request: Request,
    paper_id: int,
    payload: ExportPreviewRequest,
    current_user: PapersReadDep,
):
    paper = get_paper_or_404(paper_id)
    questions = build_export_questions(paper, payload.questionOrder, payload.includeAnswer and has_permission(current_user, "answers:read"))
    return envelope(
        {
            "paper": paper.model_dump(mode="json"),
            "questions": questions,
            "renderHint": payload.model_dump(),
        },
        request,
    )


@router.get("/{paper_id}/download")
async def download_paper(
    paper_id: int,
    current_user: PapersReadDep,
    format: str = Query(default="docx", pattern="^docx$"),
    questionOrder: QuestionOrder = QuestionOrder.paper,
    includeAnswer: bool = True,
):
    paper = get_paper_or_404(paper_id)
    questions = build_export_questions(paper, questionOrder, includeAnswer and has_permission(current_user, "answers:read"))
    file_bytes = build_paper_docx(paper, questions, include_answer=includeAnswer and has_permission(current_user, "answers:read"))
    filename = docx_filename(paper.title)
    ascii_filename = docx_filename(paper.title.encode("ascii", "ignore").decode("ascii") or "examination-paper")

    return Response(
        content=file_bytes,
        media_type=DOCX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f"attachment; filename=\"{ascii_filename}\"; filename*=UTF-8''{quote(filename)}",
            "X-Export-Format": format,
        },
    )
