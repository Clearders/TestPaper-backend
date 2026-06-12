from __future__ import annotations

import base64
import binascii
from pathlib import Path

from fastapi import HTTPException, status

from testpaper_backend.schemas import ImageUploadPayload, ImageUploadResponse

MAX_AVATAR_BYTES = 500 * 1024
PNG_SIGNATURE = bytes((137, 80, 78, 71, 13, 10, 26, 10))
AVATAR_UPLOAD_DIR = Path(__file__).resolve().parents[1] / "avatars"


def store_avatar(payload: ImageUploadPayload, user_public_id: str) -> ImageUploadResponse:
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

    if len(image_bytes) > MAX_AVATAR_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"code": "PAYLOAD_TOO_LARGE", "message": "Avatar image must be 500KB or smaller"},
        )

    if not image_bytes.startswith(PNG_SIGNATURE):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "VALIDATION_ERROR", "message": "Image data must be a PNG file"},
        )

    AVATAR_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = f"{user_public_id}.png"
    (AVATAR_UPLOAD_DIR / safe_name).write_bytes(image_bytes)
    return ImageUploadResponse(
        url=f"/api/v1/avatars/{safe_name}",
        filename=safe_name,
        mimeType=payload.mimeType,
    )
