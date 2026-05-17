from __future__ import annotations

from fastapi import APIRouter, Request

from testpaper_backend.api.dependencies import QuestionsWriteDep
from testpaper_backend.core.responses import envelope
from testpaper_backend.schemas import ImageUploadPayload
from testpaper_backend.services.images import store_uploaded_png

router = APIRouter(prefix="/api/v1/images", tags=["images"])


@router.post("/upload")
async def upload_image(
    request: Request,
    payload: ImageUploadPayload,
    current_user: QuestionsWriteDep,
):
    return envelope(store_uploaded_png(payload).model_dump(mode="json"), request)
