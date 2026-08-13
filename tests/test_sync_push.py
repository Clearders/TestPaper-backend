from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import HTTPException

from testpaper_backend.db import (
    SyncChangeLogRow,
    SyncEntityRow,
    SyncEntityVersionRow,
    SyncIdempotencyBatchRow,
    SyncOperationResultRow,
)
from testpaper_backend.schemas import (
    SyncEntityType,
    SyncErrorCode,
    SyncMutation,
    SyncMutationKind,
    SyncOperationResult,
    SyncOperationStatus,
    SyncPushRequest,
    SyncPushResponse,
    UserEntity,
    UserRole,
)
from testpaper_backend.services import sync_push

NOW = datetime(2026, 8, 13, tzinfo=UTC)
HASH_A = "a" * 64
OPERATION_1 = "11111111-1111-4111-8111-111111111111"
OPERATION_2 = "22222222-2222-4222-8222-222222222222"
ENTITY_ID = "33333333-3333-4333-8333-333333333333"
BATCH_ID = "44444444-4444-4444-8444-444444444444"


class FakeSession:
    def __init__(self, scalar_results: list[object | None] | None = None) -> None:
        self.scalar_results = list(scalar_results or [])
        self.added: list[object] = []
        self.commits = 0
        self.rollbacks = 0
        self._next_id = 1

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def scalar(self, _statement):
        return self.scalar_results.pop(0)

    def add(self, row: Any) -> None:
        if hasattr(row, "id") and getattr(row, "id", None) is None:
            row.id = self._next_id
            self._next_id += 1
        self.added.append(row)

    def flush(self) -> None:
        return None

    def begin_nested(self):
        return nullcontext()

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def teacher() -> UserEntity:
    return UserEntity(
        id=7,
        publicId="77777777-7777-4777-8777-777777777777",
        username="teacher",
        displayName="Teacher",
        role=UserRole.teacher,
        permissions=[
            "answers:read",
            "banks:delete",
            "banks:publish",
            "banks:read",
            "banks:subscribe",
            "banks:write",
            "papers:read",
            "papers:write",
            "questions:delete",
            "questions:read",
            "questions:write",
        ],
        isActive=True,
        createdAt=NOW,
        updatedAt=NOW,
    )


def create_mutation(*, operation_id: str = OPERATION_1, depends_on: list[str] | None = None) -> SyncMutation:
    return SyncMutation(
        operationId=operation_id,
        entityType="question",
        entityId=ENTITY_ID,
        kind="create",
        payload={"text": "2 + 2?", "answer": 4},
        dependsOn=depends_on or [],
    )


def push_request(*mutations: SyncMutation) -> SyncPushRequest:
    return SyncPushRequest(
        protocolVersion=1,
        batchId=BATCH_ID,
        deviceId="desktop-1",
        mutations=list(mutations),
    )


def settled_batch(payload: SyncPushRequest, response: SyncPushResponse) -> SyncIdempotencyBatchRow:
    return SyncIdempotencyBatchRow(
        id=1,
        owner_id=7,
        device_id="desktop-1",
        idempotency_key=BATCH_ID,
        request_hash=sync_push._request_hash(payload),
        protocol_version=1,
        status="completed",
        request_id="request-original",
        response_status=200,
        response_payload=response.model_dump(mode="json"),
        expires_at=NOW + timedelta(days=90),
        created_at=NOW,
        last_replayed_at=NOW,
        completed_at=NOW,
    )


def test_create_writes_projection_version_and_change_in_one_operation() -> None:
    session = FakeSession([None, None])
    mutation = create_mutation()

    result = sync_push._apply_mutation(session, user=teacher(), device_id="desktop-1", mutation=mutation)

    assert result.status == SyncOperationStatus.applied
    assert result.entityVersion == 1
    assert len(result.contentHash or "") == 64
    assert [type(row) for row in session.added] == [SyncEntityRow, SyncEntityVersionRow, SyncChangeLogRow]
    projection = session.added[0]
    assert isinstance(projection, SyncEntityRow)
    assert projection.owner_id == 7
    assert projection.public_id == ENTITY_ID
    assert projection.tombstone is False


def test_stale_update_returns_conflict_without_writes() -> None:
    current = SyncEntityRow(
        id=1,
        owner_id=7,
        entity_type="question",
        public_id=ENTITY_ID,
        scope="personal",
        schema_version=1,
        version=3,
        content_hash=HASH_A,
        payload={"text": "current"},
        tombstone=False,
        created_at=NOW,
        updated_at=NOW,
        deleted_at=None,
    )
    session = FakeSession([None, current])
    mutation = SyncMutation(
        operationId=OPERATION_1,
        entityType="question",
        entityId=ENTITY_ID,
        kind="update",
        baseVersion=2,
        baseContentHash="b" * 64,
        payload={"text": "stale"},
        dependsOn=[],
    )

    result = sync_push._apply_mutation(session, user=teacher(), device_id="desktop-1", mutation=mutation)

    assert result.status == SyncOperationStatus.conflict
    assert result.entityVersion == 3
    assert result.error is not None and result.error.code == "SYNC_CONFLICT"
    assert session.added == []


def test_unsupported_entity_schema_is_rejected_without_writes() -> None:
    session = FakeSession([None, None])
    mutation = create_mutation()
    mutation.payload = {"schemaVersion": 2, "text": "future"}

    result = sync_push._apply_mutation(session, user=teacher(), device_id="desktop-1", mutation=mutation)

    assert result.status == SyncOperationStatus.rejected
    assert result.error is not None and result.error.code == "SYNC_ENTITY_SCHEMA_UNSUPPORTED"
    assert session.added == []


def test_exact_batch_replay_returns_original_result_and_extends_retention(monkeypatch) -> None:
    payload = push_request(create_mutation())
    original = SyncPushResponse(
        protocolVersion=1,
        batchId=BATCH_ID,
        results=[SyncOperationResult(operationId=OPERATION_1, status="applied", entityVersion=1, contentHash=HASH_A)],
    )
    batch = settled_batch(payload, original)
    session = FakeSession()
    monkeypatch.setattr(sync_push, "SessionLocal", lambda: session)
    monkeypatch.setattr(sync_push, "_load_batch", lambda *_args, **_kwargs: batch)

    replay = sync_push.push_mutations(
        payload,
        user=teacher(),
        authenticated_device_id="desktop-1",
        request_id="request-replay",
    )

    assert replay == original
    assert session.commits == 1
    assert batch.last_replayed_at > NOW
    assert batch.expires_at > NOW + timedelta(days=89)


def test_reusing_batch_id_with_different_content_is_rejected(monkeypatch) -> None:
    original_payload = push_request(create_mutation())
    original = SyncPushResponse(protocolVersion=1, batchId=BATCH_ID, results=[])
    batch = settled_batch(original_payload, original)
    changed = push_request(
        SyncMutation(
            operationId=OPERATION_1,
            entityType="question",
            entityId=ENTITY_ID,
            kind="create",
            payload={"text": "different"},
            dependsOn=[],
        )
    )
    monkeypatch.setattr(sync_push, "SessionLocal", lambda: FakeSession())
    monkeypatch.setattr(sync_push, "_load_batch", lambda *_args, **_kwargs: batch)

    with pytest.raises(HTTPException) as raised:
        sync_push.push_mutations(
            changed,
            user=teacher(),
            authenticated_device_id="desktop-1",
            request_id="request-replay",
        )

    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "SYNC_IDEMPOTENCY_MISMATCH"


def test_failed_dependency_is_not_attempted_and_is_stored(monkeypatch) -> None:
    first = create_mutation()
    second = create_mutation(operation_id=OPERATION_2, depends_on=[OPERATION_1])
    payload = push_request(first, second)
    session = FakeSession()
    calls: list[str] = []

    monkeypatch.setattr(sync_push, "SessionLocal", lambda: session)
    monkeypatch.setattr(sync_push, "_load_batch", lambda *_args, **_kwargs: None)

    def apply(_session, *, user, device_id, mutation):
        calls.append(mutation.operationId)
        return SyncOperationResult(
            operationId=mutation.operationId,
            status=SyncOperationStatus.conflict,
            error=sync_push._error("SYNC_CONFLICT", "conflict"),
        )

    monkeypatch.setattr(sync_push, "_apply_mutation", apply)

    response = sync_push.push_mutations(
        payload,
        user=teacher(),
        authenticated_device_id="desktop-1",
        request_id="request-1",
    )

    assert calls == [OPERATION_1]
    assert [result.status for result in response.results] == [
        SyncOperationStatus.conflict,
        SyncOperationStatus.dependency_failed,
    ]
    stored_results = [row for row in session.added if isinstance(row, SyncOperationResultRow)]
    assert [row.status for row in stored_results] == ["conflict", "dependency_failed"]
    assert session.commits == 1


def test_batch_limits_and_device_binding_use_stable_errors(monkeypatch) -> None:
    monkeypatch.setattr(sync_push, "MAX_PUSH_MUTATIONS", 0)
    with pytest.raises(HTTPException) as too_large:
        sync_push.push_mutations(
            push_request(create_mutation()),
            user=teacher(),
            authenticated_device_id="desktop-1",
            request_id="request-1",
        )
    assert too_large.value.detail["code"] == "SYNC_BATCH_TOO_LARGE"

    monkeypatch.setattr(sync_push, "MAX_PUSH_MUTATIONS", 100)
    with pytest.raises(HTTPException) as wrong_device:
        sync_push.push_mutations(
            push_request(create_mutation()),
            user=teacher(),
            authenticated_device_id="other-device",
            request_id="request-1",
        )
    assert wrong_device.value.detail["code"] == "SYNC_ENTITY_FORBIDDEN"


def test_public_push_enums_match_the_pinned_sync_v1_contract() -> None:
    assert {value.value for value in SyncEntityType} == {
        "question",
        "paper",
        "draft",
        "attachment",
        "comment",
        "favorite",
        "setting",
    }
    assert {value.value for value in SyncMutationKind} == {
        "create",
        "update",
        "delete",
        "restore",
        "rename",
        "attach",
        "detach",
    }
    assert {value.value for value in SyncOperationStatus} == {
        "applied",
        "noop",
        "conflict",
        "rejected",
        "dependencyFailed",
    }
    assert {value.value for value in SyncErrorCode} == {
        "SYNC_PROTOCOL_UNSUPPORTED",
        "SYNC_BATCH_INVALID",
        "SYNC_BATCH_TOO_LARGE",
        "SYNC_IDEMPOTENCY_MISMATCH",
        "SYNC_DEPENDENCY_FAILED",
        "SYNC_CONFLICT",
        "SYNC_CURSOR_INVALID",
        "SYNC_CURSOR_EXPIRED",
        "SYNC_SNAPSHOT_EXPIRED",
        "SYNC_ENTITY_FORBIDDEN",
        "SYNC_ENTITY_NOT_FOUND",
        "SYNC_ENTITY_SCHEMA_UNSUPPORTED",
        "SYNC_UPLOAD_EXPIRED",
        "SYNC_UPLOAD_CHUNK_MISMATCH",
        "SYNC_UPLOAD_INCOMPLETE",
        "SYNC_ATTACHMENT_HASH_MISMATCH",
    }
