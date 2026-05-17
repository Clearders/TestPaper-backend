from __future__ import annotations

from time import perf_counter
from typing import Any, cast

from fastapi import APIRouter, Request
from sqlalchemy import text

from testpaper_backend.core.responses import envelope
from testpaper_backend.db import engine

router = APIRouter(prefix="/api/v1/health", tags=["health"])


@router.get("/postgres")
async def postgres_health(request: Request):
    try:
        if engine is None:
            raise RuntimeError("DATABASE_URL is not configured.")
        start = perf_counter()
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            version = connection.execute(text("SELECT version()")).scalar_one_or_none()
        latency_ms = round((perf_counter() - start) * 1000, 2)
        return envelope(
            {"status": "connected", "postgresVersion": version, "latencyMs": latency_ms},
            request,
        )
    except Exception as exc:
        return envelope(
            {"status": "disconnected", "error": str(exc)},
            request,
        )


@router.get("/redis")
async def redis_health(request: Request):
    try:
        from testpaper_backend.redis_client import get_redis

        client = get_redis()
        start = perf_counter()
        client.ping()
        latency_ms = round((perf_counter() - start) * 1000, 2)
        info = cast(dict[str, Any], client.info(section="server"))
        return envelope(
            {"status": "connected", "redisVersion": info.get("redis_version"), "latencyMs": latency_ms},
            request,
        )
    except Exception as exc:
        return envelope(
            {"status": "disconnected", "error": str(exc)},
            request,
        )

