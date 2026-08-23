from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from testpaper_backend.config import get_database_url


def exercise_sync_push(test_engine) -> int:
    from fastapi import HTTPException

    from testpaper_backend.db import (
        SyncChangeLogRow,
        SyncConflictRow,
        SyncDeviceCursorRow,
        SyncEntityVersionRow,
        SyncOperationResultRow,
        SyncStreamRow,
        UserRow,
    )
    from testpaper_backend.schemas import (
        SyncAckRequest,
        SyncConflictResolutionRequest,
        SyncMutation,
        SyncOperationStatus,
        SyncPushRequest,
        SyncVersionRestoreRequest,
        UserEntity,
        UserRole,
    )
    from testpaper_backend.security import permissions_for_role
    from testpaper_backend.services import sync_conflicts, sync_push, sync_read
    from testpaper_backend.time_utils import now_utc

    sessions = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)
    now = now_utc()
    with sessions() as session:
        user_row = UserRow(
            username="sync-smoke",
            display_name="Sync Smoke",
            password_hash="not-used",
            role=UserRole.teacher.value,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        session.add(user_row)
        session.commit()
        session.refresh(user_row)
        user = UserEntity(
            id=user_row.id,
            publicId=user_row.public_id,
            username=user_row.username,
            displayName=user_row.display_name,
            role=UserRole.teacher,
            permissions=permissions_for_role(UserRole.teacher),
            isActive=True,
            createdAt=now,
            updatedAt=now,
        )

    sync_push.SessionLocal = sessions
    sync_read.SessionLocal = sessions
    entity_id = "11111111-1111-4111-8111-111111111111"
    create_payload = SyncPushRequest(
        protocolVersion=1,
        batchId="22222222-2222-4222-8222-222222222222",
        deviceId="migration-smoke",
        mutations=[
            SyncMutation(
                operationId="33333333-3333-4333-8333-333333333333",
                entityType="question",
                entityId=entity_id,
                kind="create",
                payload={"text": "original", "answer": 4},
                dependsOn=[],
            )
        ],
    )
    created = sync_push.push_mutations(
        create_payload,
        user=user,
        authenticated_device_id="migration-smoke",
        request_id="sync-smoke-create",
    )
    assert created.results[0].status == SyncOperationStatus.applied
    assert created.results[0].entityVersion == 1
    base_hash = created.results[0].contentHash
    assert base_hash is not None

    replayed = sync_push.push_mutations(
        create_payload,
        user=user,
        authenticated_device_id="migration-smoke",
        request_id="sync-smoke-replay",
    )
    assert replayed == created
    with sessions() as session:
        assert session.query(SyncEntityVersionRow).count() == 1
        assert session.query(SyncChangeLogRow).count() == 1

    mismatched = create_payload.model_copy(deep=True)
    mismatched.mutations[0].payload = {"text": "changed reuse", "answer": 4}
    try:
        sync_push.push_mutations(
            mismatched,
            user=user,
            authenticated_device_id="migration-smoke",
            request_id="sync-smoke-mismatch",
        )
    except HTTPException as error:
        assert error.detail["code"] == "SYNC_IDEMPOTENCY_MISMATCH"
    else:
        raise AssertionError("changed idempotency replay unexpectedly succeeded")

    def concurrent_update(batch_id: str, operation_id: str, text_value: str):
        return sync_push.push_mutations(
            SyncPushRequest(
                protocolVersion=1,
                batchId=batch_id,
                deviceId="migration-smoke",
                mutations=[
                    SyncMutation(
                        operationId=operation_id,
                        entityType="question",
                        entityId=entity_id,
                        kind="update",
                        baseVersion=1,
                        baseContentHash=base_hash,
                        payload={"text": text_value, "answer": 4},
                        dependsOn=[],
                    )
                ],
            ),
            user=user,
            authenticated_device_id="migration-smoke",
            request_id=f"sync-smoke-{text_value}",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                concurrent_update,
                "44444444-4444-4444-8444-444444444444",
                "55555555-5555-4555-8555-555555555555",
                "device-a",
            ),
            executor.submit(
                concurrent_update,
                "66666666-6666-4666-8666-666666666666",
                "77777777-7777-4777-8777-777777777777",
                "device-b",
            ),
        ]
        concurrent_results = [future.result().results[0] for future in futures]
        statuses = sorted(result.status.value for result in concurrent_results)
    assert statuses == ["applied", "conflict"]
    applied_update = next(result for result in concurrent_results if result.status == SyncOperationStatus.applied)
    detected_conflict = next(result for result in concurrent_results if result.status == SyncOperationStatus.conflict)
    assert applied_update.entityVersion == 2 and applied_update.contentHash is not None
    assert detected_conflict.conflictId is not None

    deleted = sync_push.push_mutations(
        SyncPushRequest(
            protocolVersion=1,
            batchId="88888888-8888-4888-8888-888888888888",
            deviceId="migration-smoke",
            mutations=[
                SyncMutation(
                    operationId="99999999-9999-4999-8999-999999999999",
                    entityType="question",
                    entityId=entity_id,
                    kind="delete",
                    baseVersion=2,
                    baseContentHash=applied_update.contentHash,
                    payload=None,
                    dependsOn=[],
                )
            ],
        ),
        user=user,
        authenticated_device_id="migration-smoke",
        request_id="sync-smoke-delete",
    )
    assert deleted.results[0].status == SyncOperationStatus.applied
    assert deleted.results[0].entityVersion == 3 and deleted.results[0].contentHash is not None
    restored = sync_push.push_mutations(
        SyncPushRequest(
            protocolVersion=1,
            batchId="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            deviceId="migration-smoke",
            mutations=[
                SyncMutation(
                    operationId="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                    entityType="question",
                    entityId=entity_id,
                    kind="restore",
                    baseVersion=3,
                    baseContentHash=deleted.results[0].contentHash,
                    payload={"text": "restored", "answer": 4},
                    dependsOn=[],
                )
            ],
        ),
        user=user,
        authenticated_device_id="migration-smoke",
        request_id="sync-smoke-restore",
    )
    assert restored.results[0].status == SyncOperationStatus.applied
    assert restored.results[0].entityVersion == 4

    first_page = sync_read.pull_changes(user=user, device_id="migration-smoke", cursor=None, page_size=2)
    repeated_first_page = sync_read.pull_changes(user=user, device_id="migration-smoke", cursor=None, page_size=2)
    assert repeated_first_page == first_page
    assert first_page.hasMore is True
    assert [change.kind.value for change in first_page.changes] == ["create", "update"]
    first_ack = sync_read.acknowledge_cursor(
        SyncAckRequest(protocolVersion=1, deviceId="migration-smoke", cursor=first_page.nextCursor),
        user=user,
        device_id="migration-smoke",
    )
    assert first_ack.advanced is True
    second_page = sync_read.pull_changes(user=user, device_id="migration-smoke", cursor=first_page.nextCursor, page_size=2)
    assert second_page.hasMore is False
    assert [change.kind.value for change in second_page.changes] == ["delete", "restore"]
    second_ack = sync_read.acknowledge_cursor(
        SyncAckRequest(protocolVersion=1, deviceId="migration-smoke", cursor=second_page.nextCursor),
        user=user,
        device_id="migration-smoke",
    )
    assert second_ack.advanced is True
    repeated_ack = sync_read.acknowledge_cursor(
        SyncAckRequest(protocolVersion=1, deviceId="migration-smoke", cursor=first_page.nextCursor),
        user=user,
        device_id="migration-smoke",
    )
    assert repeated_ack.advanced is False

    with sessions() as session:
        stream = session.get(SyncStreamRow, (user.id, "personal"))
        assert stream is not None
        stream.retained_from_sequence = 3
        session.commit()
    try:
        sync_read.pull_changes(user=user, device_id="migration-smoke", cursor=first_page.nextCursor, page_size=2)
    except HTTPException as error:
        assert error.detail["code"] == "SYNC_CURSOR_EXPIRED"
    else:
        raise AssertionError("expired cursor unexpectedly produced an incremental page")

    snapshot = sync_read.snapshot_entities(user=user, device_id="migration-smoke", cursor=None, page_size=1)
    assert snapshot.hasMore is False
    assert len(snapshot.entries) == 1
    assert snapshot.entries[0].version == 4
    recovered = sync_read.acknowledge_cursor(
        SyncAckRequest(protocolVersion=1, deviceId="migration-smoke", cursor=snapshot.resumeCursor),
        user=user,
        device_id="migration-smoke",
    )
    assert recovered.advanced is False
    with sessions() as session:
        assert session.query(SyncEntityVersionRow).count() == 4
        assert session.query(SyncChangeLogRow).count() == 4
        cursor_row = session.get(SyncDeviceCursorRow, (user.id, "migration-smoke", "personal"))
        assert cursor_row is not None and cursor_row.cursor_sequence == 4
    sync_conflicts.SessionLocal = sessions
    resolved = sync_conflicts.resolve_conflict(
        detected_conflict.conflictId,
        SyncConflictResolutionRequest(
            protocolVersion=1,
            operationId="abababab-abab-4bab-8bab-abababababab",
            action="keepLocal",
            currentVersion=4,
            currentContentHash=restored.results[0].contentHash,
        ),
        user=user,
        device_id="migration-smoke",
    )
    replayed_resolution = sync_conflicts.resolve_conflict(
        detected_conflict.conflictId,
        SyncConflictResolutionRequest(
            protocolVersion=1,
            operationId="abababab-abab-4bab-8bab-abababababab",
            action="keepLocal",
            currentVersion=4,
            currentContentHash=restored.results[0].contentHash,
        ),
        user=user,
        device_id="migration-smoke",
    )
    assert replayed_resolution == resolved and resolved.acceptedVersion == 5
    undone = sync_conflicts.resolve_conflict(
        detected_conflict.conflictId,
        SyncConflictResolutionRequest(
            protocolVersion=1,
            operationId="acacacac-acac-4cac-8cac-acacacacacac",
            action="undo",
            currentVersion=5,
            currentContentHash=resolved.acceptedContentHash,
            undoesResolutionId=resolved.resolutionId,
        ),
        user=user,
        device_id="migration-smoke",
    )
    assert undone.acceptedVersion == 6 and undone.undoesResolutionId == resolved.resolutionId
    restored_history = sync_conflicts.restore_version(
        "question",
        entity_id,
        1,
        SyncVersionRestoreRequest(
            protocolVersion=1,
            operationId="adadadad-adad-4dad-8dad-adadadadadad",
            currentVersion=6,
            currentContentHash=undone.acceptedContentHash,
        ),
        user=user,
        device_id="migration-smoke",
    )
    replayed_history = sync_conflicts.restore_version(
        "question",
        entity_id,
        1,
        SyncVersionRestoreRequest(
            protocolVersion=1,
            operationId="adadadad-adad-4dad-8dad-adadadadadad",
            currentVersion=6,
            currentContentHash=undone.acceptedContentHash,
        ),
        user=user,
        device_id="migration-smoke",
    )
    assert restored_history == replayed_history and restored_history.acceptedVersion == 7
    concurrent_request = SyncConflictResolutionRequest(
        protocolVersion=1,
        operationId="aeaeaeae-aeae-4eae-8eae-aeaeaeaeaeae",
        action="keepLocal",
        currentVersion=7,
        currentContentHash=restored_history.acceptedContentHash,
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        concurrent = list(
            executor.map(
                lambda _: sync_conflicts.resolve_conflict(
                    detected_conflict.conflictId,
                    concurrent_request,
                    user=user,
                    device_id="migration-smoke",
                ),
                range(2),
            )
        )
    assert concurrent[0] == concurrent[1] and concurrent[0].acceptedVersion == 8
    try:
        sync_conflicts.resolve_conflict(
            detected_conflict.conflictId,
            SyncConflictResolutionRequest(
                protocolVersion=1,
                operationId=concurrent_request.operationId,
                action="useCloud",
                currentVersion=concurrent_request.currentVersion,
                currentContentHash=concurrent_request.currentContentHash,
            ),
            user=user,
            device_id="migration-smoke",
        )
    except HTTPException as error:
        assert error.detail["code"] == "SYNC_IDEMPOTENCY_MISMATCH"
    else:
        raise AssertionError("resolution operationId accepted different content")
    assert len(sync_conflicts.list_versions("question", entity_id, user=user)) == 8

    divergent_entity_id = "b1b1b1b1-b1b1-41b1-81b1-b1b1b1b1b1b1"
    divergent_requests = [
        SyncPushRequest(
            protocolVersion=1,
            batchId=batch_id,
            deviceId="migration-smoke",
            mutations=[
                SyncMutation(
                    operationId=operation_id,
                    entityType="question",
                    entityId=divergent_entity_id,
                    kind="create",
                    payload={"text": text_value, "answer": 4},
                    dependsOn=[],
                )
            ],
        )
        for batch_id, operation_id, text_value in (
            (
                "b2b2b2b2-b2b2-42b2-82b2-b2b2b2b2b2b2",
                "b3b3b3b3-b3b3-43b3-83b3-b3b3b3b3b3b3",
                "concurrent-create-a",
            ),
            (
                "b4b4b4b4-b4b4-44b4-84b4-b4b4b4b4b4b4",
                "b5b5b5b5-b5b5-45b5-85b5-b5b5b5b5b5b5",
                "concurrent-create-b",
            ),
        )
    ]
    with ThreadPoolExecutor(max_workers=2) as executor:
        divergent_results = list(
            executor.map(
                lambda request: sync_push.push_mutations(
                    request,
                    user=user,
                    authenticated_device_id="migration-smoke",
                    request_id=f"sync-smoke-{request.batchId}",
                ),
                divergent_requests,
            )
        )
    divergent_operation_results = [response.results[0] for response in divergent_results]
    assert sorted(result.status.value for result in divergent_operation_results) == ["applied", "conflict"]
    conflict_index = next(
        index for index, result in enumerate(divergent_operation_results) if result.status == SyncOperationStatus.conflict
    )
    concurrent_create_conflict = divergent_operation_results[conflict_index]
    assert concurrent_create_conflict.conflictId is not None, concurrent_create_conflict.model_dump(mode="json")
    replayed_concurrent_create = sync_push.push_mutations(
        divergent_requests[conflict_index],
        user=user,
        authenticated_device_id="migration-smoke",
        request_id="sync-smoke-concurrent-create-replay",
    )
    assert replayed_concurrent_create == divergent_results[conflict_index]
    with sessions() as session:
        persisted_conflict = session.scalar(
            select(SyncConflictRow).where(SyncConflictRow.public_id == concurrent_create_conflict.conflictId)
        )
        persisted_result = session.scalar(
            select(SyncOperationResultRow).where(
                SyncOperationResultRow.owner_id == user.id,
                SyncOperationResultRow.operation_id == divergent_requests[conflict_index].mutations[0].operationId,
            )
        )
        assert persisted_conflict is not None and persisted_conflict.reason == "concurrentCreate"
        assert persisted_result is not None and persisted_result.details["conflictId"] == concurrent_create_conflict.conflictId

    identical_entity_id = "b6b6b6b6-b6b6-46b6-86b6-b6b6b6b6b6b6"
    identical_requests = [
        SyncPushRequest(
            protocolVersion=1,
            batchId=batch_id,
            deviceId="migration-smoke",
            mutations=[
                SyncMutation(
                    operationId=operation_id,
                    entityType="question",
                    entityId=identical_entity_id,
                    kind="create",
                    payload={"text": "same concurrent create", "answer": 4},
                    dependsOn=[],
                )
            ],
        )
        for batch_id, operation_id in (
            ("b7b7b7b7-b7b7-47b7-87b7-b7b7b7b7b7b7", "b8b8b8b8-b8b8-48b8-88b8-b8b8b8b8b8b8"),
            ("b9b9b9b9-b9b9-49b9-89b9-b9b9b9b9b9b9", "bababaab-baba-4aba-8aba-bababaababab"),
        )
    ]
    with ThreadPoolExecutor(max_workers=2) as executor:
        identical_results = list(
            executor.map(
                lambda request: sync_push.push_mutations(
                    request,
                    user=user,
                    authenticated_device_id="migration-smoke",
                    request_id=f"sync-smoke-{request.batchId}",
                ),
                identical_requests,
            )
        )
    assert sorted(response.results[0].status.value for response in identical_results) == ["applied", "noop"]
    assert all(response.results[0].conflictId is None for response in identical_results)
    return user.id


def exercise_sync_compaction(test_engine) -> None:
    from fastapi import HTTPException
    from sqlalchemy.exc import DBAPIError

    from testpaper_backend.db import SyncChangeLogRow, SyncEntityVersionRow, SyncStreamRow, UserRow
    from testpaper_backend.schemas import SyncMutation, SyncPushRequest, UserEntity, UserRole
    from testpaper_backend.security import permissions_for_role
    from testpaper_backend.services import sync_compaction, sync_push, sync_read
    from testpaper_backend.time_utils import now_utc

    sessions = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)
    current_time = now_utc()
    with sessions() as session:
        user_row = UserRow(
            username="sync-compaction-smoke",
            display_name="Sync Compaction Smoke",
            password_hash="not-used",
            role=UserRole.teacher.value,
            is_active=True,
            created_at=current_time,
            updated_at=current_time,
        )
        session.add(user_row)
        session.commit()
        session.refresh(user_row)
        user = UserEntity(
            id=user_row.id,
            publicId=user_row.public_id,
            username=user_row.username,
            displayName=user_row.display_name,
            role=UserRole.teacher,
            permissions=permissions_for_role(UserRole.teacher),
            isActive=True,
            createdAt=current_time,
            updatedAt=current_time,
        )

    sync_push.SessionLocal = sessions
    sync_read.SessionLocal = sessions
    device_id = "compaction-smoke"

    def push(entity_id: str, kind: str, payload, *, base_version=None, base_hash=None):
        request = SyncPushRequest(
            protocolVersion=1,
            batchId=str(uuid4()),
            deviceId=device_id,
            mutations=[
                SyncMutation(
                    operationId=str(uuid4()),
                    entityType="question",
                    entityId=entity_id,
                    kind=kind,
                    baseVersion=base_version,
                    baseContentHash=base_hash,
                    payload=payload,
                    dependsOn=[],
                )
            ],
        )
        return sync_push.push_mutations(
            request,
            user=user,
            authenticated_device_id=device_id,
            request_id=f"sync-compaction-{request.batchId}",
        ).results[0]

    entity_a = "c1c1c1c1-c1c1-41c1-81c1-c1c1c1c1c1c1"
    entity_b = "c2c2c2c2-c2c2-42c2-82c2-c2c2c2c2c2c2"
    entity_c = "c3c3c3c3-c3c3-43c3-83c3-c3c3c3c3c3c3"
    entity_recent = "c4c4c4c4-c4c4-44c4-84c4-c4c4c4c4c4c4"
    original_now_utc = sync_push.now_utc
    sync_push.now_utc = lambda: current_time - timedelta(days=91)
    try:
        a_created = push(entity_a, "create", {"text": "old-a-v1"})
        a_updated = push(
            entity_a,
            "update",
            {"text": "old-a-v2"},
            base_version=a_created.entityVersion,
            base_hash=a_created.contentHash,
        )
        b_created = push(entity_b, "create", {"text": "old-b"})
        push(
            entity_b,
            "delete",
            None,
            base_version=b_created.entityVersion,
            base_hash=b_created.contentHash,
        )
        push(entity_c, "create", {"text": "old-unchanged"})
    finally:
        sync_push.now_utc = original_now_utc
    recent = push(entity_recent, "create", {"text": "recent"})
    assert a_updated.entityVersion == 2 and recent.entityVersion == 1

    first_snapshot = sync_read.snapshot_entities(user=user, device_id=device_id, cursor=None, page_size=2)
    old_snapshot_cursor = first_snapshot.nextCursor
    before_entries = list(first_snapshot.entries)
    page = first_snapshot
    while page.hasMore:
        page = sync_read.snapshot_entities(user=user, device_id=device_id, cursor=page.nextCursor, page_size=2)
        before_entries.extend(page.entries)
    with sessions() as session:
        stream = session.get(SyncStreamRow, (user.id, "personal"))
        assert stream is not None
        old_change_cursor = sync_read._change_cursor(owner_id=user.id, device_id=device_id, stream=stream, sequence=0)

    dry_run = sync_compaction.compact_sync_stream(
        owner_id=user.id,
        scope="personal",
        current_time=current_time,
        session_factory=sessions,
    )
    assert dry_run.applied is False and dry_run.deletedRows == 2 and dry_run.preservedRows == 3
    applied = sync_compaction.compact_sync_stream(
        owner_id=user.id,
        scope="personal",
        current_time=current_time,
        apply=True,
        session_factory=sessions,
    )
    assert applied.applied is True and applied.deletedRows == 2 and applied.epochRotated is True

    try:
        sync_read.pull_changes(user=user, device_id=device_id, cursor=old_change_cursor, page_size=10)
    except HTTPException as error:
        assert error.detail["code"] == "SYNC_CURSOR_EXPIRED"
        assert error.detail["details"] == {
            "snapshotUrl": "/api/v1/sync/snapshot",
            "oldestRetainedSequence": str(applied.newRetainedFromSequence),
        }
    else:
        raise AssertionError("pre-compaction change cursor unexpectedly remained valid")

    try:
        sync_read.snapshot_entities(user=user, device_id=device_id, cursor=old_snapshot_cursor, page_size=2)
    except HTTPException as error:
        assert error.detail["code"] == "SYNC_SNAPSHOT_EXPIRED"
    else:
        raise AssertionError("pre-compaction snapshot cursor unexpectedly remained valid")

    fresh_snapshot = sync_read.snapshot_entities(user=user, device_id=device_id, cursor=None, page_size=2)
    after_entries = list(fresh_snapshot.entries)
    page = fresh_snapshot
    while page.hasMore:
        page = sync_read.snapshot_entities(user=user, device_id=device_id, cursor=page.nextCursor, page_size=2)
        after_entries.extend(page.entries)
    assert [entry.model_dump(mode="json") for entry in after_entries] == [entry.model_dump(mode="json") for entry in before_entries]
    tombstone = next(entry for entry in after_entries if entry.entityId == entity_b)
    assert tombstone.kind.value == "delete" and tombstone.snapshot is None

    with sessions() as session:
        stream = session.get(SyncStreamRow, (user.id, "personal"))
        assert stream is not None
        assert stream.retained_from_sequence == applied.newRetainedFromSequence
        assert stream.snapshot_version == 1
        assert session.query(SyncEntityVersionRow).filter(SyncEntityVersionRow.owner_id == user.id).count() == 6
        remaining_changes = session.query(SyncChangeLogRow).filter(SyncChangeLogRow.owner_id == user.id).all()
        assert len(remaining_changes) == 4
        assert {row.public_id for row in remaining_changes} == {entity_a, entity_b, entity_c, entity_recent}
        horizon_cursor = sync_read._change_cursor(
            owner_id=user.id,
            device_id=device_id,
            stream=stream,
            sequence=stream.retained_from_sequence,
        )
    incremental = sync_read.pull_changes(user=user, device_id=device_id, cursor=horizon_cursor, page_size=10)
    assert [change.entityId for change in incremental.changes] == [entity_recent]
    assert incremental.hasMore is False

    immutable_attempts = [
        (
            'UPDATE sync_change_log SET "operationId" = :replacement WHERE "ownerId" = :owner_id AND "publicId" = :public_id',
            {"replacement": str(uuid4()), "owner_id": user.id, "public_id": entity_a},
        ),
        (
            'DELETE FROM sync_change_log WHERE "ownerId" = :owner_id AND "publicId" = :public_id',
            {"owner_id": user.id, "public_id": entity_c},
        ),
        (
            'DELETE FROM sync_change_log WHERE "ownerId" = :owner_id AND "publicId" = :public_id',
            {"owner_id": user.id, "public_id": entity_recent},
        ),
    ]
    for statement, parameters in immutable_attempts:
        try:
            with test_engine.begin() as connection:
                connection.execute(text(statement), parameters)
        except DBAPIError as error:
            assert "append-only" in str(error.orig)
        else:
            raise AssertionError("unsafe sync change-log mutation unexpectedly succeeded")


def exercise_conflict_model(test_engine, owner_id: int) -> None:
    from sqlalchemy.exc import DBAPIError

    snapshot = json.dumps(
        {
            "schemaVersion": 1,
            "version": 4,
            "contentHash": "a" * 64,
            "mutationKind": "update",
            "tombstone": False,
            "payload": {"text": "candidate"},
            "deviceId": "migration-smoke",
            "modifiedAt": "2026-08-13T00:00:00Z",
        },
        separators=(",", ":"),
    )
    with test_engine.begin() as connection:
        entity_id = connection.scalar(
            text('SELECT id FROM sync_entities WHERE "ownerId" = :owner_id AND "entityType" = \'question\''),
            {"owner_id": owner_id},
        )
        version_id = connection.scalar(
            text('SELECT id FROM sync_entity_versions WHERE "entityId" = :entity_id AND version = 4'),
            {"entity_id": entity_id},
        )
        assert entity_id is not None and version_id is not None
        conflict_id = connection.scalar(
            text(
                'INSERT INTO sync_conflicts ("publicId", "ownerId", "entityId", "entityType", origin, reason, '
                '"baseSnapshot", "localSnapshot", "cloudSnapshot", "detectedAt") VALUES '
                "(:public_id, :owner_id, :entity_id, 'question', 'personalSync', 'divergentContent', "
                "CAST(:snapshot AS jsonb), CAST(:snapshot AS jsonb), CAST(:snapshot AS jsonb), clock_timestamp()) RETURNING id"
            ),
            {
                "public_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
                "owner_id": owner_id,
                "entity_id": entity_id,
                "snapshot": snapshot,
            },
        )
        connection.execute(
            text(
                'INSERT INTO sync_conflict_resolutions ("publicId", "conflictId", "ownerId", "operationId", '
                '"requestHash", action, "actorDeviceId", "acceptedVersionId", "resultSnapshot", "resolvedAt") VALUES '
                "(:public_id, :conflict_id, :owner_id, :operation_id, :request_hash, 'manualMerge', 'migration-smoke', "
                ":version_id, CAST(:snapshot AS jsonb), clock_timestamp())"
            ),
            {
                "public_id": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
                "conflict_id": conflict_id,
                "owner_id": owner_id,
                "operation_id": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
                "request_hash": "e" * 64,
                "version_id": version_id,
                "snapshot": snapshot,
            },
        )

    immutable_attempts = [
        "UPDATE sync_conflicts SET reason = 'renameDivergence' WHERE \"publicId\" = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'",
        "DELETE FROM sync_conflict_resolutions WHERE \"publicId\" = 'dddddddd-dddd-4ddd-8ddd-dddddddddddd'",
        f'UPDATE sync_entity_versions SET "deviceId" = \'rewritten\' WHERE "entityId" = {entity_id} AND version = 4',
        'DELETE FROM sync_change_log WHERE "entityVersionId" = ' + str(version_id),
    ]
    for statement in immutable_attempts:
        try:
            with test_engine.begin() as connection:
                connection.execute(text(statement))
        except DBAPIError as error:
            assert "append-only" in str(error.orig)
        else:
            raise AssertionError("conflict audit history was mutable")


def exercise_attachment_model(test_engine, owner_id: int) -> None:
    from sqlalchemy.exc import IntegrityError

    digest = "a" * 64
    now_sql = "clock_timestamp()"
    with test_engine.begin() as connection:
        target_id = connection.scalar(
            text('SELECT id FROM sync_entities WHERE "ownerId" = :owner_id AND "entityType" = \'question\''),
            {"owner_id": owner_id},
        )
        assert target_id is not None
        attachment_entity_id = connection.scalar(
            text(
                'INSERT INTO sync_entities ("ownerId", "entityType", "publicId", scope, "schemaVersion", version, '
                '"contentHash", payload, tombstone, "createdAt", "updatedAt", "deletedAt") '
                f"VALUES (:owner_id, 'attachment', 'cccccccc-cccc-4ccc-8ccc-cccccccccccc', 'personal', 1, 1, "
                f":entity_hash, '{{}}'::jsonb, false, {now_sql}, {now_sql}, NULL) RETURNING id"
            ),
            {"owner_id": owner_id, "entity_hash": "c" * 64},
        )
        tampered_attachment_entity_id = connection.scalar(
            text(
                'INSERT INTO sync_entities ("ownerId", "entityType", "publicId", scope, "schemaVersion", version, '
                '"contentHash", payload, tombstone, "createdAt", "updatedAt", "deletedAt") '
                f"VALUES (:owner_id, 'attachment', 'dddddddd-dddd-4ddd-8ddd-dddddddddddd', 'personal', 1, 1, "
                f":entity_hash, '{{}}'::jsonb, false, {now_sql}, {now_sql}, NULL) RETURNING id"
            ),
            {"owner_id": owner_id, "entity_hash": "d" * 64},
        )
        blob_id = connection.scalar(
            text(
                "INSERT INTO attachment_blobs "
                '(sha256, "byteSize", "contentType", "storageKey", status, "referenceCount", '
                '"verifiedAt", "gcEligibleAt", "createdAt", "updatedAt") '
                f"VALUES (:digest, 4, 'image/png', 'blobs/aa/test', 'available', 0, {now_sql}, NULL, {now_sql}, {now_sql}) "
                "RETURNING id"
            ),
            {"digest": digest},
        )
        reference_id = connection.scalar(
            text(
                "INSERT INTO attachment_references "
                '("publicId", "ownerId", scope, "attachmentEntityId", "targetEntityId", "blobId", "contentHash", "byteSize", '
                '"fileName", "contentType", availability, tombstone, "createdAt", "updatedAt", "deletedAt", "retentionUntil") '
                f"VALUES ('cccccccc-cccc-4ccc-8ccc-cccccccccccc', :owner_id, 'personal', "
                ":attachment_entity_id, :target_id, :blob_id, :digest, 4, "
                f"'diagram.png', 'image/png', 'available', false, {now_sql}, {now_sql}, NULL, NULL) RETURNING id"
            ),
            {
                "owner_id": owner_id,
                "attachment_entity_id": attachment_entity_id,
                "target_id": target_id,
                "blob_id": blob_id,
                "digest": digest,
            },
        )
        assert (
            connection.scalar(
                text('SELECT "referenceCount" FROM attachment_blobs WHERE id = :blob_id'),
                {"blob_id": blob_id},
            )
            == 1
        )

        try:
            with connection.begin_nested():
                connection.execute(
                    text(
                        "INSERT INTO attachment_blobs "
                        '(sha256, "byteSize", "storageKey", status, "referenceCount", "verifiedAt", '
                        f'"gcEligibleAt", "createdAt", "updatedAt") VALUES (:digest, 4, \'blobs/aa/duplicate\', '
                        f"'available', 0, {now_sql}, NULL, {now_sql}, {now_sql})"
                    ),
                    {"digest": digest},
                )
        except IntegrityError:
            pass
        else:
            raise AssertionError("duplicate attachment digest unexpectedly created a second blob")

        try:
            with connection.begin_nested():
                connection.execute(
                    text(
                        "INSERT INTO attachment_references "
                        '("publicId", "ownerId", scope, "attachmentEntityId", "targetEntityId", "blobId", "contentHash", "byteSize", '
                        '"fileName", "contentType", availability, tombstone, "createdAt", "updatedAt", "deletedAt", "retentionUntil") '
                        f"VALUES ('dddddddd-dddd-4ddd-8ddd-dddddddddddd', :owner_id, 'personal', "
                        ":attachment_entity_id, :target_id, :blob_id, :wrong, 4, "
                        f"'tampered.png', 'image/png', 'available', false, {now_sql}, {now_sql}, NULL, NULL)"
                    ),
                    {
                        "owner_id": owner_id,
                        "attachment_entity_id": tampered_attachment_entity_id,
                        "target_id": target_id,
                        "blob_id": blob_id,
                        "wrong": "b" * 64,
                    },
                )
        except IntegrityError:
            pass
        else:
            raise AssertionError("reference accepted bytes whose digest did not match its metadata")

        try:
            with connection.begin_nested():
                connection.execute(
                    text(
                        "INSERT INTO attachment_references "
                        '("publicId", "ownerId", scope, "attachmentEntityId", "targetEntityId", "blobId", "contentHash", "byteSize", '
                        '"fileName", "contentType", availability, tombstone, "createdAt", "updatedAt", "deletedAt", "retentionUntil") '
                        f"VALUES ('ffffffff-ffff-4fff-8fff-ffffffffffff', :owner_id, 'personal', "
                        ":attachment_entity_id, :target_id, :blob_id, :digest, 4, "
                        f"'wrong-id.png', 'image/png', 'available', false, {now_sql}, {now_sql}, NULL, NULL)"
                    ),
                    {
                        "owner_id": owner_id,
                        "attachment_entity_id": attachment_entity_id,
                        "target_id": target_id,
                        "blob_id": blob_id,
                        "digest": digest,
                    },
                )
        except IntegrityError:
            pass
        else:
            raise AssertionError("attachment reference diverged from its Sync metadata identity")

        other_owner_id = connection.scalar(
            text(
                'INSERT INTO users ("publicId", username, "displayName", "passwordHash", role, "isActive", created_at, updated_at) '
                f"VALUES ('eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee', 'attachment-intruder', 'Attachment Intruder', "
                f"'not-used', 'teacher', true, {now_sql}, {now_sql}) RETURNING id"
            )
        )
        try:
            with connection.begin_nested():
                connection.execute(
                    text(
                        "INSERT INTO attachment_references "
                        '("publicId", "ownerId", scope, "attachmentEntityId", "targetEntityId", "blobId", "contentHash", "byteSize", '
                        '"fileName", "contentType", availability, tombstone, "createdAt", "updatedAt", "deletedAt", "retentionUntil") '
                        f"VALUES ('cccccccc-cccc-4ccc-8ccc-cccccccccccc', :other_owner_id, 'personal', "
                        ":attachment_entity_id, :target_id, :blob_id, :digest, 4, "
                        f"'stolen.png', 'image/png', 'available', false, {now_sql}, {now_sql}, NULL, NULL)"
                    ),
                    {
                        "other_owner_id": other_owner_id,
                        "attachment_entity_id": attachment_entity_id,
                        "target_id": target_id,
                        "blob_id": blob_id,
                        "digest": digest,
                    },
                )
        except IntegrityError:
            pass
        else:
            raise AssertionError("attachment reference widened the target entity owner ACL")

        retention_until = connection.scalar(text("SELECT clock_timestamp() + interval '30 days'"))
        connection.execute(
            text(
                'UPDATE attachment_references SET tombstone = true, "deletedAt" = clock_timestamp(), '
                '"retentionUntil" = :retention_until, "updatedAt" = clock_timestamp() WHERE id = :reference_id'
            ),
            {"retention_until": retention_until, "reference_id": reference_id},
        )
        reference_count, gc_eligible_at = connection.execute(
            text('SELECT "referenceCount", "gcEligibleAt" FROM attachment_blobs WHERE id = :blob_id'),
            {"blob_id": blob_id},
        ).one()
        assert reference_count == 0
        assert gc_eligible_at >= retention_until


def exercise_attachment_transfer(test_engine, owner_id: int) -> None:
    from fastapi import HTTPException

    from testpaper_backend.db import UserRow
    from testpaper_backend.schemas import AttachmentUploadInitiateRequest, UserEntity, UserRole
    from testpaper_backend.security import permissions_for_role
    from testpaper_backend.services import attachment_maintenance, attachment_transfers
    from testpaper_backend.services.attachment_storage import FilesystemAttachmentStorage

    sessions = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)
    attachment_ids = [
        "12121212-1212-4212-8212-121212121212",
        "13131313-1313-4313-8313-131313131313",
        "14141414-1414-4414-8414-141414141414",
    ]
    with sessions() as session:
        user_row = session.get(UserRow, owner_id)
        assert user_row is not None
        user = UserEntity(
            id=user_row.id,
            publicId=user_row.public_id,
            username=user_row.username,
            displayName=user_row.display_name,
            role=UserRole(user_row.role),
            permissions=permissions_for_role(UserRole(user_row.role)),
            isActive=user_row.is_active,
            createdAt=user_row.created_at,
            updatedAt=user_row.updated_at,
        )
        target_id = session.scalar(
            text(
                'SELECT "publicId" FROM sync_entities WHERE "ownerId" = :owner_id '
                "AND \"entityType\" = 'question' AND tombstone = false LIMIT 1"
            ),
            {"owner_id": owner_id},
        )
        assert target_id is not None
        for index, attachment_id in enumerate(attachment_ids):
            session.execute(
                text(
                    'INSERT INTO sync_entities ("ownerId", "entityType", "publicId", scope, "schemaVersion", '
                    'version, "contentHash", payload, tombstone, "createdAt", "updatedAt", "deletedAt") '
                    "VALUES (:owner_id, 'attachment', :public_id, 'personal', 1, 1, :entity_hash, "
                    "'{}'::jsonb, false, clock_timestamp(), clock_timestamp(), NULL)"
                ),
                {"owner_id": owner_id, "public_id": attachment_id, "entity_hash": str(index + 2) * 64},
            )
        session.commit()

    attachment_transfers.SessionLocal = sessions
    content = b"a" * (256 * 1024) + b"b" * 4096
    digest = hashlib.sha256(content).hexdigest()
    request = AttachmentUploadInitiateRequest(
        protocolVersion=1,
        idempotencyKey="attachment-smoke-1",
        attachmentId=attachment_ids[0],
        targetEntityId=target_id,
        contentHash=digest,
        byteSize=len(content),
        chunkSize=256 * 1024,
        fileName="migration-smoke.bin",
        contentType="application/octet-stream",
    )
    with TemporaryDirectory() as storage_root:
        storage = FilesystemAttachmentStorage(Path(storage_root))
        initiated = attachment_transfers.initiate_attachment_upload(
            request,
            user=user,
            device_id="migration-smoke",
            storage=storage,
        )
        replayed = attachment_transfers.initiate_attachment_upload(
            request,
            user=user,
            device_id="migration-smoke",
            storage=storage,
        )
        assert replayed == initiated and initiated.missingChunks == [0, 1]

        changed = request.model_copy(update={"fileName": "changed.bin"})
        try:
            attachment_transfers.initiate_attachment_upload(
                changed,
                user=user,
                device_id="migration-smoke",
                storage=storage,
            )
        except HTTPException as error:
            assert error.detail["code"] == "SYNC_IDEMPOTENCY_MISMATCH"
        else:
            raise AssertionError("attachment idempotency key accepted different content")

        first = content[: 256 * 1024]
        receipt = attachment_transfers.upload_attachment_chunk(
            upload_id=initiated.uploadId,
            ordinal=0,
            data=first,
            content_hash=hashlib.sha256(first).hexdigest(),
            user=user,
            device_id="migration-smoke",
            storage=storage,
        )
        duplicate = attachment_transfers.upload_attachment_chunk(
            upload_id=initiated.uploadId,
            ordinal=0,
            data=first,
            content_hash=hashlib.sha256(first).hexdigest(),
            user=user,
            device_id="migration-smoke",
            storage=storage,
        )
        assert receipt.duplicate is False and duplicate.duplicate is True
        try:
            attachment_transfers.complete_attachment_upload(
                upload_id=initiated.uploadId,
                protocol_version=1,
                user=user,
                device_id="migration-smoke",
                storage=storage,
            )
        except HTTPException as error:
            assert error.detail["code"] == "SYNC_UPLOAD_INCOMPLETE"
            assert error.detail["details"]["missingChunks"] == [1]
        else:
            raise AssertionError("incomplete attachment upload unexpectedly completed")

        second = content[256 * 1024 :]
        attachment_transfers.upload_attachment_chunk(
            upload_id=initiated.uploadId,
            ordinal=1,
            data=second,
            content_hash=hashlib.sha256(second).hexdigest(),
            user=user,
            device_id="migration-smoke",
            storage=storage,
        )
        completed = attachment_transfers.complete_attachment_upload(
            upload_id=initiated.uploadId,
            protocol_version=1,
            user=user,
            device_id="migration-smoke",
            storage=storage,
        )
        completion_replay = attachment_transfers.complete_attachment_upload(
            upload_id=initiated.uploadId,
            protocol_version=1,
            user=user,
            device_id="migration-smoke",
            storage=storage,
        )
        assert completed.completed is True and completion_replay == completed
        downloaded = attachment_transfers.download_attachment(
            attachment_id=attachment_ids[0],
            user=user,
            storage=storage,
        )
        assert downloaded.content == content and downloaded.content_hash == digest

        dedupe_request = request.model_copy(update={"idempotencyKey": "attachment-smoke-2", "attachmentId": attachment_ids[1]})
        deduplicated = attachment_transfers.initiate_attachment_upload(
            dedupe_request,
            user=user,
            device_id="migration-smoke",
            storage=storage,
        )
        assert deduplicated.completed is True and deduplicated.deduplicated is True

        attachment_maintenance.SessionLocal = sessions
        pending_content = b"unsynced attachment bytes"
        pending_hash = hashlib.sha256(pending_content).hexdigest()
        pending_request = request.model_copy(
            update={
                "idempotencyKey": "attachment-smoke-pending",
                "attachmentId": attachment_ids[2],
                "contentHash": pending_hash,
                "byteSize": len(pending_content),
            }
        )
        pending = attachment_transfers.initiate_attachment_upload(
            pending_request,
            user=user,
            device_id="migration-smoke",
            storage=storage,
        )
        attachment_transfers.upload_attachment_chunk(
            upload_id=pending.uploadId,
            ordinal=0,
            data=pending_content,
            content_hash=pending_hash,
            user=user,
            device_id="migration-smoke",
            storage=storage,
        )
        pending_key = storage.chunk_key(pending.uploadId, 0)
        with sessions() as session:
            current_time = session.scalar(text("SELECT clock_timestamp()"))
            first_reference = session.scalar(
                text('SELECT id FROM attachment_references WHERE "publicId" = :public_id'),
                {"public_id": attachment_ids[0]},
            )
            second_reference = session.scalar(
                text('SELECT id FROM attachment_references WHERE "publicId" = :public_id'),
                {"public_id": attachment_ids[1]},
            )
            assert current_time is not None and first_reference is not None and second_reference is not None
            session.execute(
                text(
                    'UPDATE attachment_upload_sessions SET "createdAt" = :created, "expiresAt" = :expired, '
                    '"updatedAt" = :expired WHERE "publicId" = :upload_id'
                ),
                {
                    "created": current_time - timedelta(days=2),
                    "expired": current_time - timedelta(days=1),
                    "upload_id": pending.uploadId,
                },
            )
            session.execute(
                text(
                    'UPDATE attachment_references SET tombstone = true, "deletedAt" = :past, '
                    '"retentionUntil" = :past, "updatedAt" = :past WHERE id = :reference_id'
                ),
                {"past": current_time - timedelta(days=31), "reference_id": first_reference},
            )
            session.commit()
        protected = attachment_maintenance.run_attachment_maintenance(
            storage=storage,
            current_time=current_time,
        )
        assert protected.blobs_deleted == 0 and protected.expired_uploads_marked == 1
        assert storage.verify(pending_key, content_hash=pending_hash, byte_size=len(pending_content))
        expired_cleanup = attachment_maintenance.run_attachment_maintenance(
            storage=storage,
            current_time=current_time + timedelta(days=8),
        )
        assert expired_cleanup.expired_uploads_deleted == 1
        assert not storage.verify(pending_key, content_hash=pending_hash, byte_size=len(pending_content))

        with sessions() as session:
            session.execute(
                text(
                    'UPDATE attachment_references SET tombstone = true, "deletedAt" = :past, '
                    '"retentionUntil" = :past, "updatedAt" = :past WHERE id = :reference_id'
                ),
                {"past": current_time - timedelta(days=31), "reference_id": second_reference},
            )
            session.execute(
                text(
                    'UPDATE attachment_upload_sessions SET "createdAt" = :created, "expiresAt" = :expired, '
                    '"updatedAt" = :expired WHERE "blobId" IS NOT NULL'
                ),
                {"created": current_time - timedelta(days=40), "expired": current_time - timedelta(days=31)},
            )
            session.commit()
        reclaimed = attachment_maintenance.run_attachment_maintenance(
            storage=storage,
            current_time=current_time,
        )
        assert reclaimed.blobs_deleted == 1 and reclaimed.files_deleted == 1
        with sessions() as session:
            assert session.scalar(text("SELECT COUNT(*) FROM attachment_blobs WHERE sha256 = :digest"), {"digest": digest}) == 0
            assert (
                session.scalar(
                    text(
                        'SELECT COUNT(*) FROM attachment_references WHERE "publicId" IN (:first, :second) '
                        "AND tombstone = true AND availability = 'pending' AND \"blobId\" IS NULL"
                    ),
                    {"first": attachment_ids[0], "second": attachment_ids[1]},
                )
                == 2
            )
            assert session.scalar(text("SELECT COUNT(*) FROM attachment_gc_audit")) >= 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Exercise the Alembic history against a temporary PostgreSQL database.")
    parser.add_argument("--diagnostics", type=Path, help="Write the successful round-trip report as JSON.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    project_root = Path(__file__).resolve().parents[1]
    alembic_config = Config(project_root / "alembic.ini")
    head_revision = ScriptDirectory.from_config(alembic_config).get_current_head()
    if head_revision is None:
        raise RuntimeError("Alembic migration history has no head revision.")
    admin_url = make_url(get_database_url()).update_query_dict({"connect_timeout": "5"})
    database_name = f"testpaper_migration_smoke_{uuid4().hex[:12]}"
    test_url = admin_url.set(database=database_name)
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    test_engine = None
    database_created = False

    def write_diagnostics(report: dict[str, object]) -> None:
        if args.diagnostics is None:
            return
        args.diagnostics.parent.mkdir(parents=True, exist_ok=True)
        args.diagnostics.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        database_created = True

        environment = os.environ.copy()
        environment["DATABASE_URL"] = test_url.render_as_string(hide_password=False)

        def alembic(*arguments: str) -> None:
            subprocess.run([sys.executable, "-m", "alembic", *arguments], cwd=project_root, env=environment, check=True)

        alembic("upgrade", "head")

        test_engine = create_engine(test_url)
        expected_tables = {
            "alembic_version",
            "attachment_blobs",
            "attachment_gc_audit",
            "attachment_references",
            "attachment_upload_chunks",
            "attachment_upload_sessions",
            "auth_tokens",
            "paper_questions",
            "papers",
            "paper_draft_collaborators",
            "paper_draft_comments",
            "paper_drafts",
            "question_corrections",
            "question_revisions",
            "questions",
            "sync_change_log",
            "sync_conflict_resolutions",
            "sync_conflicts",
            "sync_device_cursors",
            "sync_entities",
            "sync_entity_versions",
            "sync_idempotency_batches",
            "sync_operation_results",
            "sync_streams",
            "sync_version_restores",
            "users",
        }
        with test_engine.connect() as connection:
            tables = set(inspect(connection).get_table_names())
            assert expected_tables <= tables, expected_tables - tables
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == head_revision
            assert connection.scalar(text("SELECT COUNT(*) FROM users")) == 0
            assert connection.scalar(text("SELECT COUNT(*) FROM questions")) == 10

            connection.execute(text("SET enable_seqscan = off"))
            pull_plan = "\n".join(
                row[0]
                for row in connection.execute(
                    text(
                        "EXPLAIN (COSTS OFF) SELECT sequence FROM sync_change_log "
                        "WHERE \"ownerId\" = 1 AND scope = 'personal' AND sequence > 0 "
                        "ORDER BY sequence LIMIT 100"
                    )
                )
            )
            assert "ix_sync_change_log_pull" in pull_plan, pull_plan
            replay_plan = "\n".join(
                row[0]
                for row in connection.execute(
                    text(
                        'EXPLAIN (COSTS OFF) SELECT id FROM sync_idempotency_batches WHERE "ownerId" = 1 '
                        "AND \"deviceId\" = 'device-1' AND \"idempotencyKey\" = 'key-1'"
                    )
                )
            )
            assert "uq_sync_batches_owner_device_key" in replay_plan, replay_plan

        # Exercise the security migration with an existing plaintext token, not
        # only against a fresh empty auth_tokens table.
        test_engine.dispose()
        test_engine = None
        alembic("downgrade", "20260813_0025")
        test_engine = create_engine(test_url)
        with test_engine.begin() as connection:
            user_id = connection.scalar(
                text(
                    'INSERT INTO users ("publicId", username, "displayName", "passwordHash", role, "isActive", created_at, updated_at) '
                    "VALUES ('migration-user', 'migration-user', 'Migration user', 'not-used', 'viewer', true, now(), now()) "
                    "RETURNING id"
                )
            )
            connection.execute(
                text(
                    'INSERT INTO auth_tokens (token, "user_id", "tokenType", "deviceId", "lastSeenAt", created_at, expires_at) '
                    "VALUES (:token, :user_id, 'refresh', 'migration-device', now(), now(), now() + interval '1 day')"
                ),
                {"token": "migration-raw-refresh", "user_id": user_id},
            )
        test_engine.dispose()
        test_engine = None
        alembic("upgrade", "head")
        test_engine = create_engine(test_url)
        expected_digest = hashlib.sha256(b"migration-raw-refresh").hexdigest()
        with test_engine.connect() as connection:
            migrated = connection.execute(
                text('SELECT token, "familyId", "revokedAt" FROM auth_tokens WHERE "deviceId" = :device_id'),
                {"device_id": "migration-device"},
            ).one()
            assert migrated.token == expected_digest
            assert migrated.familyId == f"legacy-{expected_digest[:32]}"
            assert migrated.revokedAt is None

        owner_id = exercise_sync_push(test_engine)
        exercise_conflict_model(test_engine, owner_id)
        exercise_sync_compaction(test_engine)
        exercise_attachment_model(test_engine, owner_id)
        exercise_attachment_transfer(test_engine, owner_id)

        test_engine.dispose()
        test_engine = None
        alembic("downgrade", "base")
        test_engine = create_engine(test_url)
        with test_engine.connect() as connection:
            remaining_tables = set(inspect(connection).get_table_names())
            assert remaining_tables <= {"alembic_version"}, remaining_tables

        test_engine.dispose()
        test_engine = None
        alembic("upgrade", "head")
        test_engine = create_engine(test_url)
        with test_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == head_revision

        report = {
            "database": database_name,
            "downgradeClean": True,
            "head": head_revision,
            "seedQuestions": 10,
            "seedUsers": 0,
            "workflow": ["upgrade head", "downgrade 0025", "upgrade head", "downgrade base", "upgrade head"],
        }
        write_diagnostics(report)
        print(f"Migration smoke test passed upgrade -> base -> upgrade at {head_revision} ({database_name})")
    except BaseException as error:
        write_diagnostics(
            {
                "database": database_name,
                "error": f"{type(error).__name__}: {error}",
                "head": head_revision,
                "workflow": ["upgrade head", "downgrade 0025", "upgrade head", "downgrade base", "upgrade head"],
            }
        )
        raise
    finally:
        if test_engine is not None:
            test_engine.dispose()
        if database_created:
            with admin_engine.connect() as connection:
                connection.execute(
                    text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :database_name"),
                    {"database_name": database_name},
                )
                connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
        admin_engine.dispose()


if __name__ == "__main__":
    main()
