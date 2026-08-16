from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from testpaper_backend.db import SyncStreamRow
from testpaper_backend.services import sync_read

NOW = datetime(2026, 8, 13, tzinfo=UTC)


def stream(*, retained_from_sequence: int = 0, epoch: str = "11111111-1111-4111-8111-111111111111") -> SyncStreamRow:
    return SyncStreamRow(
        owner_id=7,
        scope="personal",
        epoch=epoch,
        retained_from_sequence=retained_from_sequence,
        snapshot_version=0,
        compacted_at=None,
        created_at=NOW,
        updated_at=NOW,
    )


def test_change_cursor_is_signed_and_audience_bound(monkeypatch) -> None:
    monkeypatch.setattr(sync_read, "get_sync_cursor_secret", lambda: "cursor-test-secret-with-at-least-32-characters")
    current = stream()
    cursor = sync_read._change_cursor(owner_id=7, device_id="desktop-1", stream=current, sequence=42)

    assert sync_read._validate_change_cursor(cursor, owner_id=7, device_id="desktop-1", stream=current) == 42

    with pytest.raises(HTTPException) as wrong_device:
        sync_read._validate_change_cursor(cursor, owner_id=7, device_id="desktop-2", stream=current)
    assert wrong_device.value.detail["code"] == "SYNC_CURSOR_INVALID"


def test_tampered_and_compacted_cursors_use_stable_errors(monkeypatch) -> None:
    monkeypatch.setattr(sync_read, "get_sync_cursor_secret", lambda: "cursor-test-secret-with-at-least-32-characters")
    current = stream()
    cursor = sync_read._change_cursor(owner_id=7, device_id="desktop-1", stream=current, sequence=2)
    body, signature = cursor.split(".", 1)
    replacement = "A" if signature[0] != "A" else "B"
    tampered_cursor = f"{body}.{replacement}{signature[1:]}"

    with pytest.raises(HTTPException) as tampered:
        sync_read._validate_change_cursor(tampered_cursor, owner_id=7, device_id="desktop-1", stream=current)
    assert tampered.value.detail["code"] == "SYNC_CURSOR_INVALID"

    with pytest.raises(HTTPException) as expired:
        sync_read._validate_change_cursor(cursor, owner_id=7, device_id="desktop-1", stream=stream(retained_from_sequence=3))
    assert expired.value.status_code == 410
    assert expired.value.detail["code"] == "SYNC_CURSOR_EXPIRED"


def test_epoch_rotation_expires_incremental_cursor(monkeypatch) -> None:
    monkeypatch.setattr(sync_read, "get_sync_cursor_secret", lambda: "cursor-test-secret-with-at-least-32-characters")
    cursor = sync_read._change_cursor(owner_id=7, device_id="desktop-1", stream=stream(), sequence=8)
    rotated = stream(epoch="22222222-2222-4222-8222-222222222222")

    with pytest.raises(HTTPException) as expired:
        sync_read._validate_change_cursor(cursor, owner_id=7, device_id="desktop-1", stream=rotated)
    assert expired.value.detail["code"] == "SYNC_CURSOR_EXPIRED"
