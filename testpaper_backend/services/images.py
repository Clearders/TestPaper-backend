from __future__ import annotations

import base64
import binascii
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, status

from testpaper_backend.schemas import ImageUploadPayload, ImageUploadResponse

MAX_IMAGE_UPLOAD_BYTES = 30 * 1024 * 1024
PNG_SIGNATURE = bytes((137, 80, 78, 71, 13, 10, 26, 10))
IMAGE_UPLOAD_DIR = Path(__file__).resolve().parents[1] / "uploaded-images"


def store_uploaded_png(payload: ImageUploadPayload) -> ImageUploadResponse:
    if payload.mimeType != "image/png":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "VALIDATION_ERROR", "message": "Only PNG images are supported"},
        )

    try:
        image_bytes = base64.b64decode(payload.data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "VALIDATION_ERROR", "message": "Image data must be valid base64"},
        ) from exc

    if len(image_bytes) > MAX_IMAGE_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"code": "PAYLOAD_TOO_LARGE", "message": "PNG image must be 30MB or smaller"},
        )

    if not image_bytes.startswith(PNG_SIGNATURE):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "VALIDATION_ERROR", "message": "Image data must be a PNG file"},
        )

    safe_name = f"{uuid4().hex}.png"
    (IMAGE_UPLOAD_DIR / safe_name).write_bytes(image_bytes)
    return ImageUploadResponse(url=f"/api/v1/images/files/{safe_name}", filename=safe_name, mimeType=payload.mimeType)

