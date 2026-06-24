from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status


def api_error(status_code: int, code: str, message: str, details: Any | None = None) -> HTTPException:
    detail: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        detail["details"] = details
    return HTTPException(status_code=status_code, detail=detail)


def validation_error(message: str, details: Any | None = None) -> HTTPException:
    return api_error(status.HTTP_422_UNPROCESSABLE_ENTITY, "VALIDATION_ERROR", message, details)
