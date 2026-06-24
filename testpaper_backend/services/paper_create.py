from __future__ import annotations

from typing import Any
from uuid import uuid4

from testpaper_backend.core.errors import validation_error
from testpaper_backend.repositories import PAPERS
from testpaper_backend.schemas import PaperCreate, PaperEntity, PaperGenerateRequest, PaperStatus, QuestionRef
from testpaper_backend.time_utils import now_utc

MIN_QUESTION_ERROR = "Paper must contain at least one question"


def create_paper_from_payload(payload: PaperCreate, owner_id: int | None = None) -> PaperEntity:
    return _create_draft_paper(
        title=payload.title,
        subject=payload.subject,
        duration=payload.duration,
        total_marks=payload.totalMarks,
        questions=[QuestionRef(**item.model_dump()) for item in sorted(payload.questions, key=lambda item: item.orderNo)],
        owner_id=owner_id,
    )


def generate_paper_from_result(payload: PaperGenerateRequest, generated: dict[str, Any], owner_id: int | None = None) -> PaperEntity:
    return _create_draft_paper(
        title=payload.title,
        subject=payload.subject,
        duration=payload.duration,
        total_marks=payload.totalMarks,
        questions=generated["paperQuestions"],
        owner_id=owner_id,
    )


def _create_draft_paper(
    *,
    title: str,
    subject: str,
    duration: int,
    total_marks: int,
    questions: list[QuestionRef],
    owner_id: int | None,
) -> PaperEntity:
    if not questions:
        raise validation_error(MIN_QUESTION_ERROR)
    now = now_utc()
    return PAPERS.create(
        PaperEntity(
            id=0,
            publicId=str(uuid4()),
            title=title,
            subject=subject,
            duration=duration,
            totalMarks=total_marks,
            questions=questions,
            status=PaperStatus.draft,
            ownerId=owner_id,
            createdAt=now,
            updatedAt=now,
        )
    )
