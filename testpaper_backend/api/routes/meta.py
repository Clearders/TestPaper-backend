from __future__ import annotations

from collections import Counter

from fastapi import APIRouter, Request
from sqlalchemy import select

from testpaper_backend.api.dependencies import QuestionsReadDep
from testpaper_backend.core.responses import envelope
from testpaper_backend.db import QuestionRow, SessionLocal

router = APIRouter(prefix="/api/v1/meta", tags=["metadata"])


@router.get("/subjects")
async def list_subjects(request: Request, current_user: QuestionsReadDep):
    with SessionLocal() as session:
        subjects = session.scalars(select(QuestionRow.subject).distinct().order_by(QuestionRow.subject)).all()
    return envelope(list(subjects), request)


@router.get("/tags")
async def list_tags(request: Request, current_user: QuestionsReadDep):
    with SessionLocal() as session:
        tag_lists = session.scalars(select(QuestionRow.tags)).all()
    counter = Counter(str(tag) for tags in tag_lists for tag in (tags or []) if tag is not None)
    return envelope(sorted(counter.keys()), request)
