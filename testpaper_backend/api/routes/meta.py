from __future__ import annotations

from fastapi import APIRouter, Request

from testpaper_backend.api.dependencies import QuestionsReadDep
from testpaper_backend.core.responses import envelope
from testpaper_backend.schemas import Envelope
from testpaper_backend.services.metadata import list_subjects_metadata, list_tags_metadata

router = APIRouter(prefix="/api/v1/meta", tags=["metadata"])


@router.get("/subjects", response_model=Envelope[list[str]])
def list_subjects(request: Request, current_user: QuestionsReadDep):
    return envelope(list_subjects_metadata(), request)


@router.get("/tags", response_model=Envelope[list[str]])
def list_tags(request: Request, current_user: QuestionsReadDep):
    return envelope(list_tags_metadata(), request)
