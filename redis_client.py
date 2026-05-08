from __future__ import annotations

import json
from typing import Any

import redis
from redis import Redis

from settings import get_redis_url


_redis_client: Redis | None = None


def get_redis() -> Redis:
    """Return a shared Redis client, creating it lazily on first call."""
    global _redis_client
    if _redis_client is None:
        url = get_redis_url()
        if url is None:
            raise RuntimeError("REDIS_URL is not configured.")
        _redis_client = redis.from_url(url, decode_responses=True)
        _redis_client.ping()  # Fail-fast on misconfiguration.
    return _redis_client


def close_redis() -> None:
    """Close the shared Redis client if it exists."""
    global _redis_client
    if _redis_client is not None:
        _redis_client.close()
        _redis_client = None


def cache_get(key: str) -> Any:
    """Retrieve a cached JSON value."""
    raw = get_redis().get(key)
    if raw is None:
        return None
    return json.loads(raw)


def cache_set(key: str, value: Any, ttl: int = 300) -> None:
    """Store a JSON-serializable value with a TTL (seconds)."""
    get_redis().setex(key, ttl, json.dumps(value, default=str))


def cache_delete(key: str) -> None:
    """Delete a cache key."""
    get_redis().delete(key)


def cache_delete_pattern(pattern: str) -> int:
    """Delete all keys matching a glob-style pattern. Returns count of keys removed."""
    client = get_redis()
    keys = client.keys(pattern)
    if not keys:
        return 0
    return client.delete(*keys)
