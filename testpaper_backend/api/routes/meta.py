from __future__ import annotations

import json

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from testpaper_backend.api.dependencies import QuestionsReadDep
from testpaper_backend.core.responses import envelope
from testpaper_backend.db import QuestionRow, SessionLocal
from testpaper_backend.redis_client import get_redis
from testpaper_backend.schemas import Envelope

router = APIRouter(prefix="/api/v1/meta", tags=["metadata"])

CACHE_TTL = 300
CACHE_KEY_SUBJECTS = "meta:subjects"
CACHE_KEY_TAGS = "meta:tags"

ALL_META_KEYS = (CACHE_KEY_SUBJECTS, CACHE_KEY_TAGS)


def invalidate_meta_cache() -> None:
    try:
        client = get_redis()
        client.delete(*ALL_META_KEYS)
    except Exception:
        pass


@router.get("/subjects", response_model=Envelope[list[str]])
def list_subjects(request: Request, current_user: QuestionsReadDep):
    try:
        client = get_redis()
        cached = client.get(CACHE_KEY_SUBJECTS)
        if cached is not None:
            return envelope(json.loads(cached), request)
    except Exception:
        pass

    with SessionLocal() as session:
        subjects = session.scalars(
            select(func.jsonb_array_elements_text(QuestionRow.subjects).label("value"))
            .distinct()
            .order_by("value")
        ).all()
    data = list(subjects)
    try:
        client = get_redis()
        client.setex(CACHE_KEY_SUBJECTS, CACHE_TTL, json.dumps(data))
    except Exception:
        pass
    return envelope(data, request)


@router.get("/tags", response_model=Envelope[list[str]])
def list_tags(request: Request, current_user: QuestionsReadDep):
    try:
        client = get_redis()
        cached = client.get(CACHE_KEY_TAGS)
        if cached is not None:
            return envelope(json.loads(cached), request)
    except Exception:
        pass

    with SessionLocal() as session:
        tags = session.scalars(
            select(func.jsonb_array_elements_text(QuestionRow.tags).label("value"))
            .distinct()
            .order_by("value")
        ).all()
    data = list(tags)
    try:
        client = get_redis()
        client.setex(CACHE_KEY_TAGS, CACHE_TTL, json.dumps(data))
    except Exception:
        pass
    return envelope(data, request)
