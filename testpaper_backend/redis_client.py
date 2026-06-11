from __future__ import annotations

import json
from typing import Any

import redis
from redis import Redis

from testpaper_backend.config import get_redis_url

_redis_client: Redis | None = None


def get_redis() -> Redis:
    """Return a shared Redis client, creating it lazily on first call."""
    global _redis_client
    if _redis_client is None:
        url = get_redis_url()
        _redis_client = redis.from_url(url, decode_responses=True)
        _redis_client.ping()  # Fail-fast on misconfiguration.
    return _redis_client


def close_redis() -> None:
    """Close the shared Redis client if it exists."""
    global _redis_client
    if _redis_client is not None:
        _redis_client.close()
        _redis_client = None
