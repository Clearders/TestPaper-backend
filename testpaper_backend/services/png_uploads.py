from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from pathlib import Path

from fastapi import status

from testpaper_backend.core.errors import api_error, validation_error
from testpaper_backend.schemas import ImageUploadPayload, ImageUploadResponse

PNG_SIGNATURE = bytes((137, 80, 78, 71, 13, 10, 26, 10))


@dataclass(frozen=True, slots=True)
class PngUploadTarget:
    directory: Path
    public_path: str
    max_bytes: int
    too_large_message: str


def store_png_upload(payload: ImageUploadPayload, target: PngUploadTarget, filename: str) -> ImageUploadResponse:
    image_bytes = decode_png_payload(payload, target)
    target.directory.mkdir(parents=True, exist_ok=True)
    safe_name = filename if filename.endswith(".png") else f"{filename}.png"
    (target.directory / safe_name).write_bytes(image_bytes)
    return ImageUploadResponse(url=f"{target.public_path}/{safe_name}", filename=safe_name, mimeType=payload.mimeType)


def decode_png_payload(payload: ImageUploadPayload, target: PngUploadTarget) -> bytes:
    if payload.mimeType != "image/png":
        raise validation_error("Only PNG images are supported")

    try:
        image_bytes = base64.b64decode(payload.data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise validation_error("Image data must be valid base64") from exc

    if len(image_bytes) > target.max_bytes:
        raise api_error(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "PAYLOAD_TOO_LARGE", target.too_large_message)

    if not image_bytes.startswith(PNG_SIGNATURE):
        raise validation_error("Image data must be a PNG file")

    return image_bytes
