from __future__ import annotations

import json
from typing import Any

from fastapi import WebSocket

from testpaper_backend.config import get_auth_cookie_name

MAX_CONNECTIONS_PER_IP = 10


def _get_websocket_ip(websocket: WebSocket) -> str:
    client = getattr(websocket, "client", None)
    if client:
        return client.host or "unknown"
    return "unknown"


class RealtimeConnectionManager:
    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._ip_connections: dict[str, set[WebSocket]] = {}

    def can_connect(self, ip: str) -> bool:
        return len(self._ip_connections.get(ip, set())) < MAX_CONNECTIONS_PER_IP

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        ip = _get_websocket_ip(websocket)
        if ip not in self._ip_connections:
            self._ip_connections[ip] = set()
        self._ip_connections[ip].add(websocket)
        self._connections.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self._connections.discard(websocket)
        ip = _get_websocket_ip(websocket)
        ip_set = self._ip_connections.get(ip)
        if ip_set:
            ip_set.discard(websocket)
            if not ip_set:
                del self._ip_connections[ip]

    async def broadcast(self, event: str, payload: dict[str, Any]) -> None:
        if not self._connections:
            return

        message = json.dumps({"event": event, "payload": payload}, default=str)
        stale: list[WebSocket] = []
        for websocket in list(self._connections):
            try:
                await websocket.send_text(message)
            except RuntimeError:
                stale.append(websocket)

        for websocket in stale:
            self.disconnect(websocket)


realtime = RealtimeConnectionManager()


def get_websocket_token(websocket: WebSocket) -> str | None:
    header = websocket.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() == "bearer" and token:
        return token
    cookie_token = websocket.cookies.get(get_auth_cookie_name())
    if cookie_token:
        return cookie_token
    return websocket.query_params.get("token")
