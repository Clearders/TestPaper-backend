from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from testpaper_backend.config import get_cors_origins, get_trusted_hosts, is_production
from testpaper_backend.core.csrf import CSRFMiddleware

Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None] | AsyncIterator[None]]


def create_app(*, lifespan: Lifespan) -> FastAPI:
    production = is_production()
    app = FastAPI(
        title="TestPaper Backend",
        version="1.0.0",
        lifespan=lifespan,
        docs_url=None if production else "/docs",
        redoc_url=None if production else "/redoc",
        openapi_url=None if production else "/openapi.json",
    )
    app.add_middleware(CSRFMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_cors_origins(),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-Id", "X-CSRF-Token"],
        expose_headers=["Content-Disposition", "X-Export-Format"],
    )
    trusted_hosts = get_trusted_hosts()
    if trusted_hosts != ["*"]:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=trusted_hosts)
    return app
