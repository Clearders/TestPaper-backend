from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from testpaper_backend.db import engine
from testpaper_backend.redis_client import close_redis, get_redis
from testpaper_backend.services.images import IMAGE_UPLOAD_DIR


@asynccontextmanager
async def lifespan(app: FastAPI):
    if engine is None:
        raise RuntimeError("DATABASE_URL is required before starting the app.")
    if engine.url.get_backend_name() == "sqlite":
        raise RuntimeError("SQLite is not supported. Set DATABASE_URL to a PostgreSQL database.")
    IMAGE_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    try:
        get_redis()
    except Exception:
        pass
    try:
        yield
    finally:
        engine.dispose()
        try:
            close_redis()
        except Exception:
            pass

