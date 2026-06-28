from __future__ import annotations

import secrets
from datetime import datetime

from fastapi import Request, Response, status
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from testpaper_backend.config import (
    get_auth_cookie_domain,
    get_auth_cookie_samesite,
    get_auth_cookie_secure,
    get_csrf_cookie_name,
)
from testpaper_backend.time_utils import now_utc

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def set_csrf_cookie(response: Response, token: str, expires_at: datetime) -> None:
    max_age = max(0, int((expires_at - now_utc()).total_seconds()))
    response.set_cookie(
        key=get_csrf_cookie_name(),
        value=token,
        max_age=max_age,
        expires=expires_at,
        path="/",
        domain=get_auth_cookie_domain(),
        secure=get_auth_cookie_secure(),
        httponly=False,
        samesite=get_auth_cookie_samesite(),
    )


def clear_csrf_cookie(response: Response) -> None:
    response.delete_cookie(
        key=get_csrf_cookie_name(),
        path="/",
        domain=get_auth_cookie_domain(),
        secure=get_auth_cookie_secure(),
        httponly=False,
        samesite=get_auth_cookie_samesite(),
    )


CSRF_EXEMPT_PATH_SUFFIXES = (
    "/auth/login",
    "/auth/register",
)


def _is_csrf_exempt(path: str) -> bool:
    normalized = path.rstrip("/")
    return normalized.endswith(CSRF_EXEMPT_PATH_SUFFIXES)


def _uses_bearer_auth(request: Request) -> bool:
    scheme, separator, token = request.headers.get("authorization", "").partition(" ")
    return separator == " " and scheme.lower() == "bearer" and bool(token.strip())


class CSRFMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)

        if request.method in SAFE_METHODS:
            await self.app(scope, receive, send)
            return

        if _is_csrf_exempt(request.url.path) or _uses_bearer_auth(request):
            await self.app(scope, receive, send)
            return

        cookie_token = request.cookies.get(get_csrf_cookie_name())
        header_token = request.headers.get("x-csrf-token")

        if not cookie_token or not header_token:
            response: JSONResponse = JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={
                    "success": False,
                    "error": {"code": "CSRF_MISSING", "message": "CSRF token missing"},
                },
            )
            await response(scope, receive, send)
            return

        if not secrets.compare_digest(cookie_token, header_token):
            response = JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={
                    "success": False,
                    "error": {"code": "CSRF_MISMATCH", "message": "CSRF token mismatch"},
                },
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
