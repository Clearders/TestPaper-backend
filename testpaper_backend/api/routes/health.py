from __future__ import annotations

from time import perf_counter
from typing import Any, cast

from fastapi import APIRouter, Request
from sqlalchemy import text

from testpaper_backend.config import is_production
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
        error_msg = "Database health check failed" if is_production() else str(exc)
        return envelope(
            {"status": "disconnected", "error": error_msg},
            request,
        )


@router.get("/redis")
async def redis_health(request: Request):
    try:
        from testpaper_backend.redis_client import get_async_redis

        client = get_async_redis()
        start = perf_counter()
        await client.ping()
        latency_ms = round((perf_counter() - start) * 1000, 2)
        info = cast(dict[str, Any], await client.info())
        result: dict[str, Any] = {
            "status": "connected",
            "redisVersion": info.get("redis_version"),
            "latencyMs": latency_ms,
            "usedMemoryHuman": info.get("used_memory_human"),
            "connectedClients": info.get("connected_clients"),
            "blockedClients": info.get("blocked_clients"),
            "keyspaceHits": info.get("keyspace_hits"),
            "keyspaceMisses": info.get("keyspace_misses"),
            "instantaneousOpsPerSec": info.get("instantaneous_ops_per_sec"),
        }
        if not is_production():
            result["uptimeInSeconds"] = info.get("uptime_in_seconds")
        return envelope(result, request)
    except Exception as exc:
        error_msg = "Redis health check failed" if is_production() else str(exc)
        return envelope(
            {"status": "disconnected", "error": error_msg},
            request,
        )

