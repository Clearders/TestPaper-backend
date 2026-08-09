from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from uuid import UUID

import pytest
from fastapi import WebSocketDisconnect
from pydantic import ValidationError

from testpaper_backend.api.routes import websocket as websocket_route
from testpaper_backend.schemas.auth import UserEntity, UserRole
from testpaper_backend.schemas.realtime import validate_client_message
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


def assert_error_message(message: str) -> None:
    envelope = json.loads(message)
    assert envelope["event"] == "error"
    assert envelope["payload"] == {"message": "test message"}
    assert UUID(envelope["eventId"])
    assert isinstance(datetime.fromisoformat(envelope["occurredAt"]), datetime)


class FailingRedis:
    async def publish(self, channel: str, message: str) -> None:
        assert channel == BROADCAST_CHANNEL
        assert json.loads(message)["event"] == "error"
        raise ConnectionError("redis unavailable")


class RouteWebSocket:
    def __init__(self, messages: list[dict[str, object]]) -> None:
        self.client = FakeClient()
        self.scope = {"client": ("127.0.0.1", 12345)}
        self.headers = {"authorization": "Bearer test-token"}
        self.cookies: dict[str, str] = {}
        self._messages = iter(messages)
        self.sent_json: list[dict[str, object]] = []

    async def send_json(self, message: dict[str, object]) -> None:
        self.sent_json.append(message)

    async def receive_text(self) -> str:
        try:
            return json.dumps(next(self._messages))
        except StopIteration as exc:
            raise WebSocketDisconnect from exc


class RecordingRealtime:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def can_connect(self, ip: str) -> bool:
        self.calls.append(("can_connect", ip))
        return True

    async def connect(self, websocket: object) -> None:
        self.calls.append(("connect", websocket))

    async def subscribe_draft(self, websocket: object, draft_id: str, user: dict[str, str]) -> None:
        self.calls.append(("subscribe", websocket, draft_id, user))

    async def update_presence(self, websocket: object, draft_id: str, activity: str) -> bool:
        self.calls.append(("update", websocket, draft_id, activity))
        return True

    async def unsubscribe_draft(self, websocket: object, draft_id: str) -> None:
        self.calls.append(("unsubscribe", websocket, draft_id))

    async def remove_socket_presence(self, websocket: object) -> None:
        self.calls.append(("remove_presence", websocket))

    def disconnect(self, websocket: object) -> None:
        self.calls.append(("disconnect", websocket))


def test_local_send_drops_stale_websocket(caplog) -> None:
    manager = RealtimeConnectionManager()
    healthy = FakeWebSocket()
    stale = FakeWebSocket(fail_send=True)
    manager._connections.update({healthy, stale})
    manager._ip_connections["127.0.0.1"] = {healthy, stale}

    caplog.set_level(logging.DEBUG, logger="testpaper_backend.services.realtime")

    asyncio.run(manager._local_send("error", {"message": "test message"}))

    assert_error_message(healthy.messages[0])
    assert healthy in manager._connections
    assert stale not in manager._connections
    assert manager._ip_connections["127.0.0.1"] == {healthy}
    assert "Dropping stale realtime websocket after send failure" in caplog.text


def test_broadcast_notifies_local_clients_when_redis_publish_fails(monkeypatch) -> None:
    manager = RealtimeConnectionManager()
    websocket = FakeWebSocket()
    manager._connections.add(websocket)
    monkeypatch.setattr(realtime_module, "get_async_redis", lambda: FailingRedis())

    asyncio.run(manager.broadcast("error", {"message": "test message"}))

    assert_error_message(websocket.messages[0])


def test_connection_limit_is_enforced_per_ip() -> None:
    manager = RealtimeConnectionManager()
    ip = "203.0.113.10"
    manager._ip_connections[ip] = {FakeWebSocket(ip) for _ in range(MAX_CONNECTIONS_PER_IP)}

    assert not manager.can_connect(ip)
    assert manager.can_connect("203.0.113.11")


def test_broadcast_to_draft_targets_only_subscribed_local_room(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = RealtimeConnectionManager()
    subscriber = FakeWebSocket()
    outsider = FakeWebSocket()
    manager._connections.update({subscriber, outsider})
    manager._draft_connections["draft-1"] = {subscriber}
    monkeypatch.setattr(realtime_module, "get_async_redis", lambda: FailingRedis())

    asyncio.run(
        manager.broadcast_to_draft(
            "draft-1",
            "draft.collaborators.updated",
            {"draftId": "draft-1", "revision": 2, "reviewStatus": "in_review", "actorId": 9},
        )
    )

    assert len(subscriber.messages) == 1
    assert not outsider.messages
    assert json.loads(subscriber.messages[0])["event"] == "draft.collaborators.updated"


def test_presence_lifecycle_falls_back_locally_and_aggregates_multitab_activity(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = RealtimeConnectionManager()
    first_tab = FakeWebSocket()
    second_tab = FakeWebSocket()
    manager._connections.update({first_tab, second_tab})
    manager._socket_drafts = {first_tab: set(), second_tab: set()}
    manager._socket_sessions = {first_tab: "session-1", second_tab: "session-2"}
    monkeypatch.setattr(realtime_module, "get_async_redis", lambda: FailingRedis())
    user = {"publicId": "user-1", "username": "ada", "displayName": "Ada"}

    async def scenario() -> None:
        await manager.subscribe_draft(first_tab, "draft-1", user)
        await manager.subscribe_draft(second_tab, "draft-1", user)
        assert await manager.update_presence(second_tab, "draft-1", "editing")
        members = await manager._presence_members("draft-1")
        assert members == [
            {
                "user": user,
                "activity": "editing",
                "lastSeenAt": members[0]["lastSeenAt"],
            }
        ]
        await manager.unsubscribe_draft(second_tab, "draft-1")
        assert ("draft-1", "session-2") not in manager._local_presence
        assert (await manager._presence_members("draft-1"))[0]["activity"] == "viewing"
        await manager.unsubscribe_draft(first_tab, "draft-1")

    asyncio.run(scenario())

    assert "draft-1" not in manager._draft_connections
    assert not manager._local_presence


def test_client_presence_message_shapes_are_discriminated_and_validated() -> None:
    subscribe = validate_client_message({"event": "draft.subscribe", "draftId": "draft-1"})
    unsubscribe = validate_client_message({"event": "draft.unsubscribe", "draftId": "draft-1"})
    update = validate_client_message({"event": "draft.presence.update", "draftId": "draft-1", "activity": "editing"})

    assert subscribe.draftId == "draft-1"
    assert unsubscribe.event == "draft.unsubscribe"
    assert update.activity == "editing"
    with pytest.raises(ValidationError):
        validate_client_message({"event": "draft.presence.update", "draftId": "draft-1", "activity": "idle"})


def test_websocket_route_dispatches_draft_subscription_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = RecordingRealtime()
    user = UserEntity(
        id=1,
        publicId="user-1",
        username="ada",
        displayName="Ada",
        role=UserRole.teacher,
        permissions=[],
        isActive=True,
        createdAt=datetime(2026, 8, 8),
        updatedAt=datetime(2026, 8, 8),
    )
    websocket = RouteWebSocket(
        [
            {"event": "draft.subscribe", "draftId": "draft-1"},
            {"event": "draft.presence.update", "draftId": "draft-1", "activity": "editing"},
            {"event": "draft.unsubscribe", "draftId": "draft-1"},
        ]
    )
    shared_draft_calls: list[tuple[str, object]] = []
    monkeypatch.setattr(websocket_route, "realtime", manager)
    monkeypatch.setattr(websocket_route, "get_user_from_token", lambda token: user)
    monkeypatch.setattr(websocket_route, "get_cors_origins", lambda: [])
    monkeypatch.setattr(
        websocket_route,
        "get_shared_draft",
        lambda draft_id, current_user: shared_draft_calls.append((draft_id, current_user)),
    )

    asyncio.run(websocket_route.websocket_endpoint(websocket))

    assert shared_draft_calls == [("draft-1", user)]
    assert [(call[0], *call[2:]) for call in manager.calls if call[0] in {"subscribe", "update", "unsubscribe"}] == [
        ("subscribe", "draft-1", {"publicId": "user-1", "username": "ada", "displayName": "Ada"}),
        ("update", "draft-1", "editing"),
        ("unsubscribe", "draft-1"),
    ]
    assert websocket.sent_json[0]["event"] == "auth.connected"
