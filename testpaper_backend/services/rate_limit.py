from __future__ import annotations

import logging

import redis
from fastapi import HTTPException, Request, status

from testpaper_backend.redis_client import get_redis

logger = logging.getLogger(__name__)

RATE_LIMIT_KEY_PREFIX = "rate_limit:"


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    client = getattr(request, "client", None)
    if client:
        return client.host or "127.0.0.1"
    return "127.0.0.1"


def check_rate_limit(key: str, max_attempts: int, window_seconds: int) -> None:
    try:
        redis_key = f"{RATE_LIMIT_KEY_PREFIX}{key}"
        client = get_redis()
        current = client.incr(redis_key)
        if current == 1:
            client.expire(redis_key, window_seconds)
        if current > max_attempts:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={"code": "RATE_LIMITED", "message": "Too many requests. Please try again later."},
            )
    except (redis.ConnectionError, redis.RedisError):
        logger.warning("Redis unavailable, rate limiting skipped for key '%s'", key)
