from __future__ import annotations

import asyncio
import json
import logging

from testpaper_backend.services import realtime as realtime_module
from testpaper_backend.services.realtime import BROADCAST_CHANNEL, MAX_CONNECTIONS_PER_IP, RealtimeConnectionManager


class FakeClient:
    def __init__(self, host: str = "127.0.0.1") -> None:
        self.host = host


class FakeWebSocket:
    def __init__(self, host: str = "127.0.0.1", *, fail_send: bool = False) -> None:
        self.client = FakeClient(host)
        self.fail_send = fail_send
        self.messages: list[str] = []

    async def send_text(self, message: str) -> None:
        if self.fail_send:
            raise RuntimeError("send failed")
        self.messages.append(message)


class FailingRedis:
    async def publish(self, channel: str, message: str) -> None:
        assert channel == BROADCAST_CHANNEL
        assert json.loads(message)["event"] == "paper.updated"
        raise ConnectionError("redis unavailable")


def test_local_send_drops_stale_websocket(caplog) -> None:
    manager = RealtimeConnectionManager()
    healthy = FakeWebSocket()
    stale = FakeWebSocket(fail_send=True)
    manager._connections.update({healthy, stale})
    manager._ip_connections["127.0.0.1"] = {healthy, stale}

    caplog.set_level(logging.DEBUG, logger="testpaper_backend.services.realtime")

    asyncio.run(manager._local_send("question.updated", {"id": 1}))

    assert json.loads(healthy.messages[0]) == {"event": "question.updated", "payload": {"id": 1}}
    assert healthy in manager._connections
    assert stale not in manager._connections
    assert manager._ip_connections["127.0.0.1"] == {healthy}
    assert "Dropping stale realtime websocket after send failure" in caplog.text


def test_broadcast_notifies_local_clients_when_redis_publish_fails(monkeypatch) -> None:
    manager = RealtimeConnectionManager()
    websocket = FakeWebSocket()
    manager._connections.add(websocket)
    monkeypatch.setattr(realtime_module, "get_async_redis", lambda: FailingRedis())

    asyncio.run(manager.broadcast("paper.updated", {"id": 1}))

    assert json.loads(websocket.messages[0]) == {"event": "paper.updated", "payload": {"id": 1}}


def test_connection_limit_is_enforced_per_ip() -> None:
    manager = RealtimeConnectionManager()
    ip = "203.0.113.10"
    manager._ip_connections[ip] = {FakeWebSocket(ip) for _ in range(MAX_CONNECTIONS_PER_IP)}

    assert not manager.can_connect(ip)
    assert manager.can_connect("203.0.113.11")
