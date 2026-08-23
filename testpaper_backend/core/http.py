from __future__ import annotations

import logging
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from testpaper_backend.config import get_auth_cookie_secure, is_production
from testpaper_backend.core.logging_config import set_request_id
from testpaper_backend.core.responses import error_envelope
from testpaper_backend.schemas.sync import MAX_SYNC_BATCH_BYTES, MAX_SYNC_MUTATION_BYTES, MAX_SYNC_MUTATIONS

logger = logging.getLogger(__name__)

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "script-src-attr 'none'; "
    "style-src 'self'; "
    "font-src 'self' data:; "
    "img-src 'self' data: blob:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "object-src 'none'; "
    "worker-src 'self'"
)


def register_request_id_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request.state.request_id = request.headers.get("x-request-id", str(uuid4()))
        set_request_id(request.state.request_id)
        response = await call_next(request)
        response.headers["x-request-id"] = request.state.request_id
        return response


def register_security_headers(app: FastAPI) -> None:
    @app.middleware("http")
    async def security_headers_middleware(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
        if is_production() and get_auth_cookie_secure():
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        sync_request = request.url.path.startswith("/api/v1/sync/")
        batch_too_large = request.url.path == "/api/v1/sync/push" and any(
            error["type"] == "too_long" and tuple(error["loc"][1:2]) == ("mutations",) for error in exc.errors()
        )
        details = (
            {
                "maxMutations": MAX_SYNC_MUTATIONS,
                "maxMutationBytes": MAX_SYNC_MUTATION_BYTES,
                "maxBatchBytes": MAX_SYNC_BATCH_BYTES,
            }
            if batch_too_large
            else [{"field": ".".join(str(item) for item in error["loc"][1:]), "reason": error["msg"]} for error in exc.errors()]
        )
        return JSONResponse(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE if batch_too_large else status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=error_envelope(
                "SYNC_BATCH_TOO_LARGE" if batch_too_large else "SYNC_BATCH_INVALID" if sync_request else "VALIDATION_ERROR",
                "Sync batch contains too many mutations" if batch_too_large else "Request validation failed",
                request,
                details=details,
            ),
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {"code": "INTERNAL_ERROR", "message": str(exc.detail)}
        return JSONResponse(
            status_code=exc.status_code,
            content=error_envelope(
                detail.get("code", "INTERNAL_ERROR"),
                detail.get("message", "An error occurred"),
                request,
                detail.get("details"),
            ),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logger.exception("Unhandled exception")
        if is_production():
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content=error_envelope("INTERNAL_ERROR", "Internal server error", request),
            )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=error_envelope("INTERNAL_ERROR", f"{type(exc).__name__}: {exc}", request),
        )
