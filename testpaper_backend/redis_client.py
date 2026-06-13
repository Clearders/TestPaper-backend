from __future__ import annotations

import redis
import redis.asyncio
from redis import Redis
from redis.asyncio import Redis as AsyncRedis

from testpaper_backend.config import (
    get_redis_max_connections,
    get_redis_socket_connect_timeout,
    get_redis_socket_timeout,
    get_redis_url,
)

_sync_pool: redis.ConnectionPool | None = None
_sync_client: Redis | None = None

_async_client: AsyncRedis | None = None


def _connection_kwargs() -> dict:
    return {
        "max_connections": get_redis_max_connections(),
        "socket_timeout": get_redis_socket_timeout(),
        "socket_connect_timeout": get_redis_socket_connect_timeout(),
        "retry_on_timeout": True,
        "decode_responses": True,
    }


def get_redis() -> Redis:
    global _sync_pool, _sync_client
    if _sync_client is None:
        url = get_redis_url()
        _sync_pool = redis.ConnectionPool.from_url(url, **_connection_kwargs())
        _sync_client = Redis(connection_pool=_sync_pool)
    return _sync_client


def get_async_redis() -> AsyncRedis:
    global _async_client
    if _async_client is None:
        url = get_redis_url()
        _async_client = redis.asyncio.from_url(url, **_connection_kwargs())
    return _async_client


def close_redis() -> None:
    global _sync_pool, _sync_client
    if _sync_client is not None:
        _sync_client.close()
        _sync_client = None
    if _sync_pool is not None:
        _sync_pool.disconnect()
        _sync_pool = None


async def close_async_redis() -> None:
    global _async_client
    if _async_client is not None:
        await _async_client.close()
        _async_client = None
