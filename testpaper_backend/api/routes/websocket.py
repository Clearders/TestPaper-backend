from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, status

from testpaper_backend.config import get_cors_origins
from testpaper_backend.security import get_user_from_token
from testpaper_backend.services.realtime import get_websocket_token, realtime
from testpaper_backend.time_utils import now_utc

router = APIRouter(prefix="/api/v1", tags=["realtime"])


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    origin = websocket.headers.get("origin", "")
    if origin and origin not in get_cors_origins():
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    try:
        current_user = get_user_from_token(get_websocket_token(websocket))
    except HTTPException:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await realtime.connect(websocket)
    try:
        await websocket.send_json(
            {
                "event": "auth.connected",
                "payload": {
                    "user": current_user.model_dump(mode="json"),
                    "serverTime": now_utc().isoformat(),
                },
            }
        )
        while True:
            raw_message = await websocket.receive_text()
            try:
                message = json.loads(raw_message)
            except json.JSONDecodeError:
                await websocket.send_json({"event": "error", "payload": {"message": "Invalid JSON message"}})
                continue

            if message.get("event") == "ping":
                await websocket.send_json({"event": "pong", "payload": {"serverTime": now_utc().isoformat()}})
    except WebSocketDisconnect:
        pass
    finally:
        realtime.disconnect(websocket)

