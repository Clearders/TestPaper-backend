from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, WebSocket, status

from testpaper_backend.config import get_auth_cookie_name
from testpaper_backend.redis_client import get_async_redis
from testpaper_backend.schemas.realtime import serialize_server_message
from testpaper_backend.security import get_user_from_token
from testpaper_backend.services.drafts import get_shared_draft

MAX_CONNECTIONS_PER_IP = 10
BROADCAST_CHANNEL = "testpaper:broadcast"
PRESENCE_TTL_SECONDS = 45
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
        self._draft_connections: dict[str, set[WebSocket]] = {}
        self._socket_drafts: dict[WebSocket, set[str]] = {}
        self._socket_sessions: dict[WebSocket, str] = {}
        self._socket_users: dict[WebSocket, dict[str, str]] = {}
        self._socket_tokens: dict[WebSocket, str] = {}
        self._local_presence: dict[tuple[str, str], dict[str, Any]] = {}
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
                            serialize_server_message(event, payload)
                        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                            logger.warning("Ignoring malformed realtime event", exc_info=True)
                            continue
                        if data.get("source") == self._source_id:
                            continue
                        relayed_message = serialize_server_message(
                            event,
                            payload,
                            event_id=data.get("eventId"),
                            occurred_at=data.get("occurredAt"),
                        )
                        audience_draft_id = data.get("audienceDraftId")
                        if isinstance(audience_draft_id, str):
                            await self._local_send(event, payload, draft_id=audience_draft_id, message=relayed_message)
                        else:
                            await self._local_send(event, payload, message=relayed_message)
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

    async def _local_send(
        self,
        event: str,
        payload: dict[str, Any],
        *,
        draft_id: str | None = None,
        message: dict[str, Any] | None = None,
    ) -> None:
        recipients = self._draft_connections.get(draft_id, set()) if draft_id else self._connections
        if not recipients:
            return
        serialized = message or serialize_server_message(event, payload)
        message_text = json.dumps(serialized, ensure_ascii=False, separators=(",", ":"))
        stale: list[WebSocket] = []
        for websocket in list(recipients):
            if not self._recipient_is_authorized(websocket, draft_id):
                with suppress(Exception):
                    await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                stale.append(websocket)
                continue
            try:
                await websocket.send_text(message_text)
            except Exception:
                logger.debug("Dropping stale realtime websocket after send failure", exc_info=True)
                stale.append(websocket)
        for ws in stale:
            self.disconnect(ws)

    def _recipient_is_authorized(self, websocket: WebSocket, draft_id: str | None) -> bool:
        token = self._socket_tokens.get(websocket)
        if token is None:
            # Unit-level manager callers may install synthetic sockets directly;
            # every production connection is registered with a token.
            return True
        try:
            user = get_user_from_token(token, _get_websocket_ip(websocket))
            if draft_id is not None:
                get_shared_draft(draft_id, user)
        except HTTPException:
            return False
        return True

    async def connect(self, websocket: WebSocket, *, token: str, user: dict[str, str]) -> None:
        await websocket.accept()
        ip = _get_websocket_ip(websocket)
        if ip not in self._ip_connections:
            self._ip_connections[ip] = set()
        self._ip_connections[ip].add(websocket)
        self._connections.add(websocket)
        self._socket_drafts[websocket] = set()
        self._socket_sessions[websocket] = uuid4().hex
        self._socket_tokens[websocket] = token
        self._socket_users[websocket] = user
        await self._ensure_pubsub()

    def disconnect(self, websocket: WebSocket) -> None:
        self._connections.discard(websocket)
        for draft_id in self._socket_drafts.pop(websocket, set()):
            room = self._draft_connections.get(draft_id)
            if room:
                room.discard(websocket)
                if not room:
                    self._draft_connections.pop(draft_id, None)
        session_id = self._socket_sessions.pop(websocket, None)
        if session_id:
            for key in [key for key in self._local_presence if key[1] == session_id]:
                self._local_presence.pop(key, None)
        self._socket_users.pop(websocket, None)
        self._socket_tokens.pop(websocket, None)
        ip = _get_websocket_ip(websocket)
        ip_set = self._ip_connections.get(ip)
        if ip_set:
            ip_set.discard(websocket)
            if not ip_set:
                del self._ip_connections[ip]

    async def broadcast(self, event: str, payload: dict[str, Any]) -> None:
        public_message = serialize_server_message(event, payload)
        await self._local_send(event, payload, message=public_message)
        await self._publish(public_message)

    async def broadcast_to_draft(self, draft_id: str, event: str, payload: dict[str, Any]) -> None:
        public_message = serialize_server_message(event, payload)
        await self._local_send(event, payload, draft_id=draft_id, message=public_message)
        await self._publish(public_message, audience_draft_id=draft_id)

    async def _publish(self, public_message: dict[str, Any], *, audience_draft_id: str | None = None) -> None:
        try:
            async_redis = get_async_redis()
            internal_message = {**public_message, "source": self._source_id}
            if audience_draft_id:
                internal_message["audienceDraftId"] = audience_draft_id
            message = json.dumps(internal_message, ensure_ascii=False, separators=(",", ":"))
            await async_redis.publish(BROADCAST_CHANNEL, message)
        except Exception:
            logger.warning("Realtime Redis publish failed; local clients were still notified", exc_info=True)

    async def subscribe_draft(self, websocket: WebSocket, draft_id: str, user: dict[str, str]) -> None:
        previous_drafts = set(self._socket_drafts.get(websocket, set()))
        for previous_draft in previous_drafts - {draft_id}:
            await self.unsubscribe_draft(websocket, previous_draft)
        self._draft_connections.setdefault(draft_id, set()).add(websocket)
        self._socket_drafts.setdefault(websocket, set()).add(draft_id)
        self._socket_users[websocket] = user
        await self.update_presence(websocket, draft_id, "viewing")

    async def evict_user_from_draft(self, draft_id: str, user_public_id: str) -> None:
        targets = [
            websocket
            for websocket in self._draft_connections.get(draft_id, set())
            if self._socket_users.get(websocket, {}).get("publicId") == user_public_id
        ]
        for websocket in targets:
            await self.unsubscribe_draft(websocket, draft_id)

    async def unsubscribe_draft(self, websocket: WebSocket, draft_id: str) -> None:
        room = self._draft_connections.get(draft_id)
        if room:
            room.discard(websocket)
            if not room:
                self._draft_connections.pop(draft_id, None)
        self._socket_drafts.setdefault(websocket, set()).discard(draft_id)
        session_id = self._socket_sessions.get(websocket)
        if session_id:
            self._local_presence.pop((draft_id, session_id), None)
            await self._remove_redis_presence(draft_id, session_id)
        await self._broadcast_presence_snapshot(draft_id)

    async def update_presence(self, websocket: WebSocket, draft_id: str, activity: str) -> bool:
        if draft_id not in self._socket_drafts.get(websocket, set()):
            return False
        session_id = self._socket_sessions[websocket]
        user = self._socket_users[websocket]
        now = datetime.now(UTC)
        entry = {
            "sessionId": session_id,
            "user": user,
            "activity": activity,
            "lastSeenAt": now.isoformat(),
        }
        self._local_presence[(draft_id, session_id)] = entry
        await self._write_redis_presence(draft_id, session_id, entry, now)
        await self._broadcast_presence_snapshot(draft_id)
        return True

    async def remove_socket_presence(self, websocket: WebSocket) -> None:
        for draft_id in list(self._socket_drafts.get(websocket, set())):
            await self.unsubscribe_draft(websocket, draft_id)

    @staticmethod
    def _presence_index_key(draft_id: str) -> str:
        return f"testpaper:presence:draft:{draft_id}"

    @staticmethod
    def _presence_session_key(session_id: str) -> str:
        return f"testpaper:presence:session:{session_id}"

    async def _write_redis_presence(
        self,
        draft_id: str,
        session_id: str,
        entry: dict[str, Any],
        now: datetime,
    ) -> None:
        try:
            async_redis = get_async_redis()
            expires_at = now + timedelta(seconds=PRESENCE_TTL_SECONDS)
            async with async_redis.pipeline(transaction=False) as pipeline:
                pipeline.set(self._presence_session_key(session_id), json.dumps(entry), ex=PRESENCE_TTL_SECONDS)
                pipeline.zadd(self._presence_index_key(draft_id), {session_id: expires_at.timestamp()})
                pipeline.expire(self._presence_index_key(draft_id), PRESENCE_TTL_SECONDS * 2)
                await pipeline.execute()
        except Exception:
            logger.warning("Redis presence update failed; using local instance presence", exc_info=True)

    async def _remove_redis_presence(self, draft_id: str, session_id: str) -> None:
        try:
            async_redis = get_async_redis()
            async with async_redis.pipeline(transaction=False) as pipeline:
                pipeline.delete(self._presence_session_key(session_id))
                pipeline.zrem(self._presence_index_key(draft_id), session_id)
                await pipeline.execute()
        except Exception:
            logger.warning("Redis presence cleanup failed; entry will expire", exc_info=True)

    async def _presence_members(self, draft_id: str) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        try:
            async_redis = get_async_redis()
            index_key = self._presence_index_key(draft_id)
            now_timestamp = datetime.now(UTC).timestamp()
            await async_redis.zremrangebyscore(index_key, "-inf", now_timestamp)
            session_ids = await async_redis.zrange(index_key, 0, -1)
            if session_ids:
                raw_entries = await async_redis.mget([self._presence_session_key(value) for value in session_ids])
                entries = [json.loads(value) for value in raw_entries if value]
        except Exception:
            logger.warning("Redis presence snapshot failed; using local instance presence", exc_info=True)
            entries = [value for (entry_draft_id, _), value in self._local_presence.items() if entry_draft_id == draft_id]

        members_by_user: dict[str, dict[str, Any]] = {}
        for entry in entries:
            user = entry.get("user")
            if not isinstance(user, dict) or not isinstance(user.get("publicId"), str):
                continue
            public_id = user["publicId"]
            current = members_by_user.get(public_id)
            if current is None:
                members_by_user[public_id] = {
                    "user": user,
                    "activity": entry.get("activity", "viewing"),
                    "lastSeenAt": entry.get("lastSeenAt"),
                }
                continue
            if entry.get("lastSeenAt", "") > current["lastSeenAt"]:
                current["lastSeenAt"] = entry.get("lastSeenAt")
                current["user"] = user
            if entry.get("activity") == "editing":
                current["activity"] = "editing"
        return sorted(members_by_user.values(), key=lambda member: (member["activity"] != "editing", member["user"]["displayName"]))

    async def _broadcast_presence_snapshot(self, draft_id: str) -> None:
        members = await self._presence_members(draft_id)
        await self.broadcast_to_draft(draft_id, "draft.presence.snapshot", {"draftId": draft_id, "members": members})

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
