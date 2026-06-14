from __future__ import annotations

import logging
from threading import Lock
from time import monotonic

from fastapi import HTTPException, Request, status

from testpaper_backend.redis_client import get_redis

logger = logging.getLogger(__name__)

RATE_LIMIT_KEY_PREFIX = "rate-limit:"
MAX_FALLBACK_COUNTERS = 10_000

_ATOMIC_INCR_WITH_EXPIRE = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return current
"""

_incr_and_expire = None
_fallback_lock = Lock()
_fallback_counters: dict[str, tuple[int, float]] = {}


def _load_incr_script():
    global _incr_and_expire
    if _incr_and_expire is None:
        _incr_and_expire = get_redis().register_script(_ATOMIC_INCR_WITH_EXPIRE)
    return _incr_and_expire


def get_client_ip(request: Request) -> str:
    # Uvicorn rewrites request.client only for proxies listed in
    # FORWARDED_ALLOW_IPS. Reading X-Forwarded-For directly would let clients
    # choose arbitrary rate-limit buckets.
    client = getattr(request, "client", None)
    if client:
        return client.host or "127.0.0.1"
    return "127.0.0.1"


def check_rate_limit(key: str, max_attempts: int, window_seconds: int) -> None:
    redis_key = f"{RATE_LIMIT_KEY_PREFIX}{key}"
    try:
        client = get_redis()
        current = int(_load_incr_script()(keys=[redis_key], args=[window_seconds], client=client))
        if current > max_attempts:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={"code": "RATE_LIMITED", "message": "Too many requests. Please try again later."},
            )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Redis rate limiting unavailable for key '%s'; using local fallback: %s", key, exc)
        _check_local_fallback(key, max_attempts, window_seconds)


def _check_local_fallback(key: str, max_attempts: int, window_seconds: int) -> None:
    now = monotonic()
    with _fallback_lock:
        current, expires_at = _fallback_counters.get(key, (0, now + window_seconds))
        if expires_at <= now:
            current, expires_at = 0, now + window_seconds
        current += 1
        _fallback_counters[key] = (current, expires_at)

        if len(_fallback_counters) > MAX_FALLBACK_COUNTERS:
            expired = [item_key for item_key, (_, expiry) in _fallback_counters.items() if expiry <= now]
            for item_key in expired:
                _fallback_counters.pop(item_key, None)
            while len(_fallback_counters) > MAX_FALLBACK_COUNTERS:
                _fallback_counters.pop(next(iter(_fallback_counters)))

    if current > max_attempts:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "RATE_LIMITED", "message": "Too many requests. Please try again later."},
        )
