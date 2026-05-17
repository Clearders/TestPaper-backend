from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from testpaper_backend.config import get_cors_origins

Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None] | AsyncIterator[None]]


def create_app(*, lifespan: Lifespan) -> FastAPI:
    app = FastAPI(title="TestPaper Backend", version="1.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition", "X-Export-Format"],
    )
    return app
