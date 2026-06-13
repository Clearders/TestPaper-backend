from __future__ import annotations

import logging

from fastapi import HTTPException, Request, status

from testpaper_backend.redis_client import get_redis

logger = logging.getLogger(__name__)

RATE_LIMIT_KEY_PREFIX = "rate-limit:"

_ATOMIC_INCR_WITH_EXPIRE = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return current
"""

_incr_and_expire = get_redis().register_script(_ATOMIC_INCR_WITH_EXPIRE)


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    client = getattr(request, "client", None)
    if client:
        return client.host or "127.0.0.1"
    return "127.0.0.1"


def check_rate_limit(key: str, max_attempts: int, window_seconds: int) -> None:
    redis_key = f"{RATE_LIMIT_KEY_PREFIX}{key}"
    try:
        client = get_redis()
        current = int(_incr_and_expire(keys=[redis_key], args=[window_seconds], client=client))
        if current > max_attempts:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={"code": "RATE_LIMITED", "message": "Too many requests. Please try again later."},
            )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Rate limiting unavailable for key '%s', allowing request: %s", key, exc)
        return
