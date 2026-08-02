from __future__ import annotations

from uuid import uuid4

from testpaper_backend.config import get_image_upload_dir
from testpaper_backend.schemas import ImageUploadPayload, ImageUploadResponse
from testpaper_backend.services.png_uploads import PngUploadTarget, store_png_upload

MAX_IMAGE_UPLOAD_BYTES = 30 * 1024 * 1024
IMAGE_UPLOAD_DIR = get_image_upload_dir()
QUESTION_IMAGE_UPLOAD = PngUploadTarget(
    directory=IMAGE_UPLOAD_DIR,
    public_path="/api/v1/images/files",
    max_bytes=MAX_IMAGE_UPLOAD_BYTES,
    too_large_message="PNG image must be 30MB or smaller",
)


def store_uploaded_png(payload: ImageUploadPayload) -> ImageUploadResponse:
    return store_png_upload(payload, QUESTION_IMAGE_UPLOAD, uuid4().hex)
