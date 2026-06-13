from __future__ import annotations

from uuid import uuid4

from fastapi import HTTPException, status

from testpaper_backend.repositories import PAPERS
from testpaper_backend.schemas import PaperCreate, PaperEntity, PaperGenerateRequest, QuestionRef, PaperStatus
from testpaper_backend.time_utils import now_utc


def create_paper_from_payload(payload: PaperCreate) -> PaperEntity:
    if not payload.questions:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "VALIDATION_ERROR", "message": "Paper must contain at least one question"},
        )
    entity = PaperEntity(
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
    return PAPERS.create(entity)


def generate_paper_from_result(payload: PaperGenerateRequest, generated: dict) -> PaperEntity:
    entity = PaperEntity(
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
    return PAPERS.create(entity)
