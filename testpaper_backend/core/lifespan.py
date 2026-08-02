from __future__ import annotations

from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from testpaper_backend.config import validate_configuration
from testpaper_backend.db import engine
from testpaper_backend.redis_client import close_async_redis, close_redis, get_async_redis, get_redis
from testpaper_backend.services.images import IMAGE_UPLOAD_DIR
from testpaper_backend.services.profiles import AVATAR_UPLOAD_DIR
from testpaper_backend.services.realtime import realtime


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_configuration()
    if engine is None:
        raise RuntimeError("DATABASE_URL is required before starting the app.")
    if engine.url.get_backend_name() == "sqlite":
        raise RuntimeError("SQLite is not supported. Set DATABASE_URL to a PostgreSQL database.")
    IMAGE_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    AVATAR_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    with suppress(Exception):
        get_redis()

    with suppress(Exception):
        get_async_redis()

    try:
        yield
    finally:
        with suppress(Exception):
            await realtime.shutdown()

        engine.dispose()

        with suppress(Exception):
            await close_async_redis()

        with suppress(Exception):
            close_redis()
