from __future__ import annotations

from typing import Any

from fastapi import Request

from testpaper_backend.schemas import Envelope, MetaInfo


def envelope(data: Any, request: Request) -> dict[str, Any]:
    return Envelope(
        data=data,
        meta=MetaInfo(requestId=request.state.request_id),
    ).model_dump(mode="json")


def error_envelope(code: str, message: str, request: Request, details: Any | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return {
        "success": False,
        "error": error,
        "meta": {"requestId": request.state.request_id},
    }
