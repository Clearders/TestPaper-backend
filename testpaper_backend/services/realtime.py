from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from typing import Any
from uuid import uuid4

from fastapi import WebSocket

from testpaper_backend.config import get_auth_cookie_name
from testpaper_backend.redis_client import get_async_redis

MAX_CONNECTIONS_PER_IP = 10
BROADCAST_CHANNEL = "testpaper:broadcast"
logger = logging.getLogger(__name__)


def _get_websocket_ip(websocket: WebSocket) -> str:
    client = getattr(websocket, "client", None)
    if client:
        return client.host or "unknown"
    return "unknown"


class RealtimeConnectionManager:
    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._ip_connections: dict[str, set[WebSocket]] = {}
        self._pubsub: Any = None
        self._pubsub_task: asyncio.Task[None] | None = None
        self._source_id = uuid4().hex

    def can_connect(self, ip: str) -> bool:
        return len(self._ip_connections.get(ip, set())) < MAX_CONNECTIONS_PER_IP

    async def _ensure_pubsub(self) -> None:
        if self._pubsub_task is not None and not self._pubsub_task.done():
            return
        self._pubsub_task = asyncio.create_task(self._listen_pubsub())

    async def _listen_pubsub(self) -> None:
        try:
            while True:
                try:
                    if self._pubsub is None:
                        async_redis = get_async_redis()
                        self._pubsub = async_redis.pubsub()
                        await self._pubsub.subscribe(BROADCAST_CHANNEL)
                    async for message in self._pubsub.listen():
                        if message["type"] != "message":
                            continue
                        try:
                            data = json.loads(message["data"])
                            event = data["event"]
                            payload = data["payload"]
                            if not isinstance(event, str) or not isinstance(payload, dict):
                                raise ValueError("Realtime event must contain a string event and object payload")
                        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                            logger.warning("Ignoring malformed realtime event", exc_info=True)
                            continue
                        if data.get("source") == self._source_id:
                            continue
                        await self._local_send(event, payload)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Realtime Redis subscriber disconnected; retrying")
                    if self._pubsub is not None:
                        with suppress(Exception):
                            await self._pubsub.close()
                        self._pubsub = None
                    await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        finally:
            if self._pubsub_task is asyncio.current_task():
                self._pubsub_task = None

    async def _local_send(self, event: str, payload: dict[str, Any]) -> None:
        if not self._connections:
            return
        message = json.dumps({"event": event, "payload": payload}, default=str)
        stale: list[WebSocket] = []
        for websocket in list(self._connections):
            try:
                await websocket.send_text(message)
            except Exception:
                stale.append(websocket)
        for ws in stale:
            self.disconnect(ws)

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        ip = _get_websocket_ip(websocket)
        if ip not in self._ip_connections:
            self._ip_connections[ip] = set()
        self._ip_connections[ip].add(websocket)
        self._connections.add(websocket)
        await self._ensure_pubsub()

    def disconnect(self, websocket: WebSocket) -> None:
        self._connections.discard(websocket)
        ip = _get_websocket_ip(websocket)
        ip_set = self._ip_connections.get(ip)
        if ip_set:
            ip_set.discard(websocket)
            if not ip_set:
                del self._ip_connections[ip]

    async def broadcast(self, event: str, payload: dict[str, Any]) -> None:
        await self._local_send(event, payload)
        try:
            async_redis = get_async_redis()
            message = json.dumps({"event": event, "payload": payload, "source": self._source_id}, default=str)
            await async_redis.publish(BROADCAST_CHANNEL, message)
        except Exception:
            logger.warning("Realtime Redis publish failed; local clients were still notified", exc_info=True)

    async def shutdown(self) -> None:
        if self._pubsub_task is not None:
            self._pubsub_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._pubsub_task
            self._pubsub_task = None
        if self._pubsub is not None:
            await self._pubsub.close()
            self._pubsub = None


realtime = RealtimeConnectionManager()


def get_websocket_token(websocket: WebSocket) -> str | None:
    header = websocket.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() == "bearer" and token:
        return token
    cookie_token = websocket.cookies.get(get_auth_cookie_name())
    if cookie_token:
        return cookie_token
    return None
