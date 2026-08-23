from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import timedelta
from typing import Any
from uuid import uuid4

from fastapi import status
from sqlalchemy import func, select, tuple_
from sqlalchemy.orm import Session

from testpaper_backend.config import get_sync_cursor_secret
from testpaper_backend.core.errors import api_error
from testpaper_backend.db import (
    SessionLocal,
    SyncChangeLogRow,
    SyncDeviceCursorRow,
    SyncEntityVersionRow,
    SyncStreamRow,
)
from testpaper_backend.schemas import (
    SyncAckRequest,
    SyncAckResponse,
    SyncChange,
    SyncPullResponse,
    SyncSnapshotResponse,
    UserEntity,
)
from testpaper_backend.schemas.sync import SYNC_PROTOCOL_VERSION, SYNC_SNAPSHOT_URL
from testpaper_backend.time_utils import as_aware_utc, now_utc

MAX_PULL_PAGE_SIZE = 500
DEFAULT_PULL_PAGE_SIZE = 100
DEVICE_CURSOR_TTL_DAYS = 90
PERSONAL_SCOPE = "personal"


def _cursor_expired(stream: SyncStreamRow, message: str):
    return api_error(
        status.HTTP_410_GONE,
        "SYNC_CURSOR_EXPIRED",
        message,
        {
            "snapshotUrl": SYNC_SNAPSHOT_URL,
            "oldestRetainedSequence": str(stream.retained_from_sequence),
        },
    )


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _encode_cursor(data: dict[str, Any]) -> str:
    body = json.dumps(data, separators=(",", ":"), sort_keys=True).encode()
    signature = hmac.new(get_sync_cursor_secret().encode(), body, hashlib.sha256).digest()
    return f"{_b64encode(body)}.{_b64encode(signature)}"


def _decode_cursor(cursor: str) -> dict[str, Any]:
    try:
        encoded_body, encoded_signature = cursor.split(".", 1)
        body = _b64decode(encoded_body)
        signature = _b64decode(encoded_signature)
        expected = hmac.new(get_sync_cursor_secret().encode(), body, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("signature mismatch")
        data = json.loads(body)
        if not isinstance(data, dict) or data.get("v") != 1:
            raise ValueError("unsupported cursor envelope")
        return data
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise api_error(status.HTTP_400_BAD_REQUEST, "SYNC_CURSOR_INVALID", "The sync cursor is invalid") from exc


def _stream(session: Session, owner_id: int) -> SyncStreamRow:
    stream = session.scalar(
        select(SyncStreamRow).where(SyncStreamRow.owner_id == owner_id, SyncStreamRow.scope == PERSONAL_SCOPE).with_for_update()
    )
    if stream is None:
        now = now_utc()
        stream = SyncStreamRow(
            owner_id=owner_id,
            scope=PERSONAL_SCOPE,
            epoch=str(uuid4()),
            retained_from_sequence=0,
            snapshot_version=0,
            compacted_at=None,
            created_at=now,
            updated_at=now,
        )
        session.add(stream)
        session.flush()
    return stream


def _device_cursor(
    session: Session,
    *,
    owner_id: int,
    device_id: str,
    stream: SyncStreamRow,
    allow_recovery: bool,
) -> SyncDeviceCursorRow:
    device = session.scalar(
        select(SyncDeviceCursorRow)
        .where(
            SyncDeviceCursorRow.owner_id == owner_id,
            SyncDeviceCursorRow.device_id == device_id,
            SyncDeviceCursorRow.scope == PERSONAL_SCOPE,
        )
        .with_for_update()
    )
    now = now_utc()
    if device is None:
        device = SyncDeviceCursorRow(
            owner_id=owner_id,
            device_id=device_id,
            scope=PERSONAL_SCOPE,
            stream_epoch=stream.epoch,
            cursor_sequence=0,
            protocol_version=SYNC_PROTOCOL_VERSION,
            last_ack_at=None,
            last_seen_at=now,
            expires_at=now + timedelta(days=DEVICE_CURSOR_TTL_DAYS),
            revoked_at=None,
            created_at=now,
            updated_at=now,
        )
        session.add(device)
        session.flush()
        return device
    if device.revoked_at is not None:
        raise api_error(status.HTTP_403_FORBIDDEN, "SYNC_ENTITY_FORBIDDEN", "The sync device is revoked")
    expired = as_aware_utc(device.expires_at) <= now or device.stream_epoch != stream.epoch
    if expired and not allow_recovery:
        raise _cursor_expired(stream, "The device cursor requires snapshot recovery")
    if expired:
        device.stream_epoch = stream.epoch
    device.last_seen_at = now
    device.expires_at = now + timedelta(days=DEVICE_CURSOR_TTL_DAYS)
    device.updated_at = now
    return device


def _change_cursor(*, owner_id: int, device_id: str, stream: SyncStreamRow, sequence: int) -> str:
    return _encode_cursor(
        {
            "v": 1,
            "purpose": "change",
            "owner": owner_id,
            "device": device_id,
            "scope": PERSONAL_SCOPE,
            "epoch": stream.epoch,
            "sequence": sequence,
        }
    )


def _validate_change_cursor(
    cursor: str,
    *,
    owner_id: int,
    device_id: str,
    stream: SyncStreamRow,
) -> int:
    data = _decode_cursor(cursor)
    if (
        data.get("purpose") != "change"
        or data.get("owner") != owner_id
        or data.get("device") != device_id
        or data.get("scope") != PERSONAL_SCOPE
    ):
        raise api_error(status.HTTP_400_BAD_REQUEST, "SYNC_CURSOR_INVALID", "The sync cursor has the wrong audience")
    if data.get("epoch") != stream.epoch:
        raise _cursor_expired(stream, "The sync cursor epoch has expired")
    sequence = data.get("sequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
        raise api_error(status.HTTP_400_BAD_REQUEST, "SYNC_CURSOR_INVALID", "The sync cursor sequence is invalid")
    if sequence < stream.retained_from_sequence:
        raise _cursor_expired(stream, "The sync cursor precedes retained history")
    return sequence


def _change(row) -> SyncChange:
    return SyncChange(
        sequence=str(row.sequence),
        entityType=row.entity_type,
        entityId=row.public_id,
        kind=row.mutation_kind,
        version=row.version,
        contentHash=row.content_hash,
        updatedAt=row.created_at,
        snapshot=row.payload,
    )


def pull_changes(
    *,
    user: UserEntity,
    device_id: str,
    cursor: str | None,
    page_size: int = DEFAULT_PULL_PAGE_SIZE,
) -> SyncPullResponse:
    page_size = min(max(page_size, 1), MAX_PULL_PAGE_SIZE)
    with SessionLocal() as session:
        stream = _stream(session, user.id)
        device = _device_cursor(session, owner_id=user.id, device_id=device_id, stream=stream, allow_recovery=False)
        sequence = (
            _validate_change_cursor(cursor, owner_id=user.id, device_id=device_id, stream=stream)
            if cursor is not None
            else device.cursor_sequence
        )
        if sequence < stream.retained_from_sequence:
            raise _cursor_expired(stream, "The device cursor precedes retained history")
        rows = session.execute(
            select(
                SyncChangeLogRow.sequence,
                SyncChangeLogRow.entity_type,
                SyncChangeLogRow.public_id,
                SyncChangeLogRow.mutation_kind,
                SyncChangeLogRow.version,
                SyncChangeLogRow.content_hash,
                SyncChangeLogRow.created_at,
                SyncEntityVersionRow.payload,
            )
            .join(SyncEntityVersionRow, SyncEntityVersionRow.id == SyncChangeLogRow.entity_version_id)
            .where(
                SyncChangeLogRow.owner_id == user.id,
                SyncChangeLogRow.scope == PERSONAL_SCOPE,
                SyncChangeLogRow.sequence > sequence,
            )
            .order_by(SyncChangeLogRow.sequence)
            .limit(page_size + 1)
        ).all()
        has_more = len(rows) > page_size
        page = rows[:page_size]
        next_sequence = page[-1].sequence if page else sequence
        response = SyncPullResponse(
            protocolVersion=SYNC_PROTOCOL_VERSION,
            changes=[_change(row) for row in page],
            nextCursor=_change_cursor(owner_id=user.id, device_id=device_id, stream=stream, sequence=next_sequence),
            hasMore=has_more,
        )
        session.commit()
        return response


def acknowledge_cursor(payload: SyncAckRequest, *, user: UserEntity, device_id: str) -> SyncAckResponse:
    if payload.protocolVersion != SYNC_PROTOCOL_VERSION:
        raise api_error(status.HTTP_426_UPGRADE_REQUIRED, "SYNC_PROTOCOL_UNSUPPORTED", "The sync protocol is unsupported")
    if payload.deviceId != device_id:
        raise api_error(status.HTTP_403_FORBIDDEN, "SYNC_ENTITY_FORBIDDEN", "deviceId does not match the access token")
    with SessionLocal() as session:
        stream = _stream(session, user.id)
        device = _device_cursor(session, owner_id=user.id, device_id=device_id, stream=stream, allow_recovery=False)
        sequence = _validate_change_cursor(payload.cursor, owner_id=user.id, device_id=device_id, stream=stream)
        advanced = sequence > device.cursor_sequence
        if advanced:
            device.cursor_sequence = sequence
            device.last_ack_at = now_utc()
            device.updated_at = device.last_ack_at
        current_cursor = _change_cursor(
            owner_id=user.id,
            device_id=device_id,
            stream=stream,
            sequence=device.cursor_sequence,
        )
        session.commit()
        return SyncAckResponse(
            protocolVersion=SYNC_PROTOCOL_VERSION,
            deviceId=device_id,
            cursor=current_cursor,
            advanced=advanced,
        )


def snapshot_entities(
    *,
    user: UserEntity,
    device_id: str,
    cursor: str | None,
    page_size: int = DEFAULT_PULL_PAGE_SIZE,
) -> SyncSnapshotResponse:
    page_size = min(max(page_size, 1), MAX_PULL_PAGE_SIZE)
    with SessionLocal() as session:
        stream = _stream(session, user.id)
        _device_cursor(session, owner_id=user.id, device_id=device_id, stream=stream, allow_recovery=True)
        if cursor is None:
            boundary = session.scalar(
                select(func.coalesce(func.max(SyncChangeLogRow.sequence), 0)).where(
                    SyncChangeLogRow.owner_id == user.id,
                    SyncChangeLogRow.scope == PERSONAL_SCOPE,
                )
            )
            snapshot_id = str(uuid4())
            last_type = None
            last_id = None
        else:
            data = _decode_cursor(cursor)
            if (
                data.get("purpose") != "snapshot"
                or data.get("owner") != user.id
                or data.get("device") != device_id
                or data.get("scope") != PERSONAL_SCOPE
            ):
                raise api_error(status.HTTP_400_BAD_REQUEST, "SYNC_CURSOR_INVALID", "The snapshot cursor has the wrong audience")
            if data.get("epoch") != stream.epoch:
                raise api_error(status.HTTP_410_GONE, "SYNC_SNAPSHOT_EXPIRED", "The snapshot cursor has expired")
            boundary = data.get("boundary")
            snapshot_id = data.get("snapshotId")
            last_type = data.get("lastType")
            last_id = data.get("lastId")
            if not isinstance(boundary, int) or not isinstance(snapshot_id, str):
                raise api_error(status.HTTP_400_BAD_REQUEST, "SYNC_CURSOR_INVALID", "The snapshot cursor is invalid")

        ranked = (
            select(
                SyncChangeLogRow.sequence.label("sequence"),
                SyncChangeLogRow.entity_type.label("entity_type"),
                SyncChangeLogRow.public_id.label("public_id"),
                SyncChangeLogRow.mutation_kind.label("mutation_kind"),
                SyncChangeLogRow.version.label("version"),
                SyncChangeLogRow.content_hash.label("content_hash"),
                SyncChangeLogRow.created_at.label("created_at"),
                SyncEntityVersionRow.payload.label("payload"),
                func.row_number()
                .over(
                    partition_by=(SyncChangeLogRow.entity_type, SyncChangeLogRow.public_id),
                    order_by=SyncChangeLogRow.sequence.desc(),
                )
                .label("entity_rank"),
            )
            .join(SyncEntityVersionRow, SyncEntityVersionRow.id == SyncChangeLogRow.entity_version_id)
            .where(
                SyncChangeLogRow.owner_id == user.id,
                SyncChangeLogRow.scope == PERSONAL_SCOPE,
                SyncChangeLogRow.sequence <= boundary,
            )
            .subquery()
        )
        query = select(ranked).where(ranked.c.entity_rank == 1)
        if last_type is not None and last_id is not None:
            query = query.where(tuple_(ranked.c.entity_type, ranked.c.public_id) > tuple_(last_type, last_id))
        rows = session.execute(query.order_by(ranked.c.entity_type, ranked.c.public_id).limit(page_size + 1)).all()
        has_more = len(rows) > page_size
        page = rows[:page_size]
        next_last_type = page[-1].entity_type if page else last_type
        next_last_id = page[-1].public_id if page else last_id
        next_cursor = _encode_cursor(
            {
                "v": 1,
                "purpose": "snapshot",
                "owner": user.id,
                "device": device_id,
                "scope": PERSONAL_SCOPE,
                "epoch": stream.epoch,
                "snapshotId": snapshot_id,
                "boundary": boundary,
                "lastType": next_last_type,
                "lastId": next_last_id,
            }
        )
        resume_cursor = _change_cursor(owner_id=user.id, device_id=device_id, stream=stream, sequence=boundary)
        response = SyncSnapshotResponse(
            protocolVersion=SYNC_PROTOCOL_VERSION,
            snapshotId=snapshot_id,
            entries=[_change(row) for row in page],
            nextCursor=next_cursor,
            hasMore=has_more,
            resumeCursor=resume_cursor,
        )
        session.commit()
        return response
