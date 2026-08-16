from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from testpaper_backend.schemas import UserEntity, UserRole
from testpaper_backend.services import sync_conflicts

NOW = datetime(2026, 8, 13, tzinfo=UTC)
CONFLICT_ID = "11111111-1111-4111-8111-111111111111"
RESOLUTION_ID = "22222222-2222-4222-8222-222222222222"
OPERATION_ID = "33333333-3333-4333-8333-333333333333"


def teacher() -> UserEntity:
    return UserEntity(
        id=7,
        publicId="77777777-7777-4777-8777-777777777777",
        username="teacher",
        displayName="Teacher",
        role=UserRole.teacher,
        permissions=["questions:read", "questions:write"],
        isActive=True,
        createdAt=NOW,
        updatedAt=NOW,
    )


class ScalarRows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class FakeSession:
    def __init__(self, conflict, resolutions=()):
        self.conflict = conflict
        self.resolutions = list(resolutions)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def scalar(self, _statement):
        return self.conflict

    def scalars(self, _statement):
        return ScalarRows(self.resolutions)


def resolution_row():
    snapshot = {
        "schemaVersion": 1,
        "version": 4,
        "contentHash": "a" * 64,
        "mutationKind": "update",
        "tombstone": False,
        "payload": {"text": "accepted"},
        "deviceId": "desktop-a",
        "modifiedAt": NOW.isoformat(),
    }
    return SimpleNamespace(
        public_id=RESOLUTION_ID,
        conflict_id=9,
        operation_id=OPERATION_ID,
        action="manualMerge",
        actor_device_id="desktop-a",
        result_snapshot=snapshot,
        new_entity_public_id=None,
        undoes_resolution_id=None,
        undoes_resolution=None,
        resolved_at=NOW,
    )


def test_resolution_history_returns_owned_append_only_records(monkeypatch) -> None:
    session = FakeSession(SimpleNamespace(id=9), [resolution_row()])
    monkeypatch.setattr(sync_conflicts, "SessionLocal", lambda: session)

    records = sync_conflicts.list_conflict_resolutions(CONFLICT_ID, user=teacher())

    assert [record.resolutionId for record in records] == [RESOLUTION_ID]
    assert records[0].action == "manualMerge"
    assert records[0].acceptedVersion == 4


def test_resolution_history_does_not_disclose_unknown_or_foreign_conflicts(monkeypatch) -> None:
    monkeypatch.setattr(sync_conflicts, "SessionLocal", lambda: FakeSession(None))

    with pytest.raises(HTTPException) as caught:
        sync_conflicts.list_conflict_resolutions(CONFLICT_ID, user=teacher())

    assert caught.value.status_code == 404
    assert caught.value.detail["code"] == "SYNC_ENTITY_NOT_FOUND"
