from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import uuid4

from fastapi import status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from testpaper_backend.core.errors import api_error
from testpaper_backend.db import (
    SessionLocal,
    SyncChangeLogRow,
    SyncConflictResolutionRow,
    SyncConflictRow,
    SyncEntityRow,
    SyncEntityVersionRow,
    SyncVersionRestoreRow,
)
from testpaper_backend.schemas import (
    SyncConflictRecord,
    SyncConflictResolutionRecord,
    SyncConflictResolutionRequest,
    SyncConflictSnapshot,
    SyncEntityType,
    SyncEntityVersionRecord,
    SyncMutation,
    SyncMutationKind,
    SyncOperationResult,
    SyncOperationStatus,
    SyncResolutionAction,
    SyncVersionRestoreRecord,
    SyncVersionRestoreRequest,
    UserEntity,
)
from testpaper_backend.services.attachment_maintenance import apply_attachment_reference_lifecycle
from testpaper_backend.services.conflict_rules import classify_sync_conflict
from testpaper_backend.time_utils import now_utc

SYNC_PROTOCOL_VERSION = 1


def _digest(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _snapshot(
    *,
    schema_version: int,
    version: int,
    content_hash: str,
    mutation_kind: str,
    tombstone: bool,
    payload: dict[str, Any] | None,
    device_id: str,
    modified_at,
) -> dict[str, Any]:
    return SyncConflictSnapshot(
        schemaVersion=schema_version,
        version=version,
        contentHash=content_hash,
        mutationKind=mutation_kind,
        tombstone=tombstone,
        payload=payload,
        deviceId=device_id,
        modifiedAt=modified_at,
    ).model_dump(mode="json")


def create_conflict(
    session,
    *,
    user: UserEntity,
    device_id: str,
    mutation: SyncMutation,
    entity: SyncEntityRow,
) -> SyncOperationResult:
    local_payload = None if mutation.kind == SyncMutationKind.delete else mutation.payload
    local_hash = _digest(local_payload)
    cloud_kind = entity.versions[-1].mutation_kind if entity.versions else ("delete" if entity.tombstone else "update")
    reason = classify_sync_conflict(
        local_kind=mutation.kind,
        cloud_kind=SyncMutationKind(cloud_kind),
        local_content_hash=local_hash,
        cloud_content_hash=entity.content_hash,
    )
    if reason is None:
        return SyncOperationResult(
            operationId=mutation.operationId,
            status=SyncOperationStatus.noop,
            entityVersion=entity.version,
            contentHash=entity.content_hash,
        )
    base_row = None
    if mutation.baseVersion is not None:
        base_row = session.scalar(
            select(SyncEntityVersionRow).where(
                SyncEntityVersionRow.entity_id == entity.id,
                SyncEntityVersionRow.owner_id == user.id,
                SyncEntityVersionRow.version == mutation.baseVersion,
            )
        )
    detected_at = now_utc()
    conflict = SyncConflictRow(
        public_id=str(uuid4()),
        owner_id=user.id,
        entity_id=entity.id,
        entity_type=entity.entity_type,
        origin="personalSync",
        reason=reason.value,
        base_snapshot=(
            _snapshot(
                schema_version=base_row.schema_version,
                version=base_row.version,
                content_hash=base_row.content_hash,
                mutation_kind=base_row.mutation_kind,
                tombstone=base_row.tombstone,
                payload=base_row.payload,
                device_id=base_row.device_id,
                modified_at=base_row.created_at,
            )
            if base_row is not None
            else None
        ),
        local_snapshot=_snapshot(
            schema_version=(local_payload or {}).get("schemaVersion", 1),
            version=(mutation.baseVersion or 0) + 1,
            content_hash=local_hash,
            mutation_kind=mutation.kind.value,
            tombstone=mutation.kind == SyncMutationKind.delete,
            payload=local_payload,
            device_id=device_id,
            modified_at=detected_at,
        ),
        cloud_snapshot=_snapshot(
            schema_version=entity.schema_version,
            version=entity.version,
            content_hash=entity.content_hash,
            mutation_kind=cloud_kind,
            tombstone=entity.tombstone,
            payload=entity.payload,
            device_id=entity.versions[-1].device_id if entity.versions else "cloud",
            modified_at=entity.updated_at,
        ),
        detected_at=detected_at,
    )
    session.add(conflict)
    session.flush()
    return SyncOperationResult(
        operationId=mutation.operationId,
        status=SyncOperationStatus.conflict,
        entityVersion=entity.version,
        contentHash=entity.content_hash,
        conflictId=conflict.public_id,
        error={
            "code": "SYNC_CONFLICT",
            "message": "Local and Cloud candidates diverged and were preserved",
            "retryable": False,
            "details": {"reason": reason.value},
        },
    )


def _record_from_row(row: SyncConflictRow) -> SyncConflictRecord:
    return SyncConflictRecord(
        protocolVersion=1,
        conflictId=row.public_id,
        origin="personalSync",
        entityType=row.entity_type,
        entityId=row.entity.public_id if hasattr(row, "entity") and row.entity else row.local_snapshot.get("entityId", ""),
        reason=row.reason,
        base=row.base_snapshot,
        local=row.local_snapshot,
        cloud=row.cloud_snapshot,
        detectedAt=row.detected_at,
    )


def get_conflict(conflict_id: str, *, user: UserEntity) -> SyncConflictRecord:
    with SessionLocal() as session:
        row = session.execute(
            select(SyncConflictRow, SyncEntityRow)
            .join(SyncEntityRow, SyncEntityRow.id == SyncConflictRow.entity_id)
            .where(SyncConflictRow.owner_id == user.id, SyncConflictRow.public_id == conflict_id)
        ).one_or_none()
        if row is None:
            raise api_error(status.HTTP_404_NOT_FOUND, "SYNC_ENTITY_NOT_FOUND", "Conflict was not found")
        conflict, entity = row
        record = _record_from_row(conflict)
        return record.model_copy(update={"entityId": entity.public_id})


def list_versions(entity_type: SyncEntityType, entity_id: str, *, user: UserEntity) -> list[SyncEntityVersionRecord]:
    entity_type = SyncEntityType(entity_type)
    with SessionLocal() as session:
        entity = session.scalar(
            select(SyncEntityRow).where(
                SyncEntityRow.owner_id == user.id,
                SyncEntityRow.entity_type == entity_type.value,
                SyncEntityRow.public_id == entity_id,
            )
        )
        if entity is None:
            raise api_error(status.HTTP_404_NOT_FOUND, "SYNC_ENTITY_NOT_FOUND", "Entity was not found")
        rows = session.scalars(
            select(SyncEntityVersionRow)
            .where(SyncEntityVersionRow.owner_id == user.id, SyncEntityVersionRow.entity_id == entity.id)
            .order_by(SyncEntityVersionRow.version.desc())
        )
        return [_version_record(row, entity_type=entity_type, entity_id=entity_id) for row in rows]


def _version_record(row, *, entity_type, entity_id) -> SyncEntityVersionRecord:
    return SyncEntityVersionRecord(
        entityType=entity_type,
        entityId=entity_id,
        version=row.version,
        schemaVersion=row.schema_version,
        contentHash=row.content_hash,
        mutationKind=row.mutation_kind,
        tombstone=row.tombstone,
        payload=row.payload,
        operationId=row.operation_id,
        deviceId=row.device_id,
        createdAt=row.created_at,
    )


def _append_version(session, *, entity, payload, tombstone, operation_id, action, device_id, now):
    base_version = entity.version
    base_hash = entity.content_hash
    content_hash = _digest(payload)
    entity.version += 1
    entity.content_hash = content_hash
    entity.payload = payload
    entity.tombstone = tombstone
    entity.deleted_at = now if tombstone else None
    entity.updated_at = now
    mutation_kind = "delete" if tombstone else "restore" if action in {"restoreVersion", "undo"} else "update"
    apply_attachment_reference_lifecycle(
        session,
        entity=entity,
        mutation_kind=mutation_kind,
        occurred_at=now,
    )
    version = SyncEntityVersionRow(
        entity_id=entity.id,
        owner_id=entity.owner_id,
        version=entity.version,
        schema_version=entity.schema_version,
        content_hash=content_hash,
        payload=payload,
        tombstone=tombstone,
        mutation_kind=mutation_kind,
        operation_id=operation_id,
        base_version=base_version,
        base_hash=base_hash,
        device_id=device_id,
        created_at=now,
    )
    session.add(version)
    session.flush()
    session.add(
        SyncChangeLogRow(
            entity_version_id=version.id,
            owner_id=entity.owner_id,
            scope=entity.scope,
            entity_type=entity.entity_type,
            public_id=entity.public_id,
            version=version.version,
            content_hash=content_hash,
            mutation_kind=version.mutation_kind,
            tombstone=tombstone,
            operation_id=operation_id,
            created_at=now,
        )
    )
    return version


def resolve_conflict(
    conflict_id: str,
    payload: SyncConflictResolutionRequest,
    *,
    user: UserEntity,
    device_id: str,
) -> SyncConflictResolutionRecord:
    request_hash = _digest(payload.model_dump(mode="json"))
    with SessionLocal() as session:
        replay = session.scalar(
            select(SyncConflictResolutionRow).where(
                SyncConflictResolutionRow.owner_id == user.id,
                SyncConflictResolutionRow.operation_id == payload.operationId,
            )
        )
        if replay is not None:
            if replay.conflict.public_id != conflict_id or replay.request_hash != request_hash:
                raise api_error(
                    status.HTTP_409_CONFLICT,
                    "SYNC_IDEMPOTENCY_MISMATCH",
                    "Operation ID content differs",
                )
            return _resolution_record(replay, replay.conflict.public_id)

        conflict = session.scalar(
            select(SyncConflictRow).where(SyncConflictRow.owner_id == user.id, SyncConflictRow.public_id == conflict_id).with_for_update()
        )
        if conflict is None:
            raise api_error(status.HTTP_404_NOT_FOUND, "SYNC_ENTITY_NOT_FOUND", "Conflict was not found")
        replay = session.scalar(
            select(SyncConflictResolutionRow).where(
                SyncConflictResolutionRow.owner_id == user.id,
                SyncConflictResolutionRow.operation_id == payload.operationId,
            )
        )
        if replay is not None:
            if replay.conflict.public_id != conflict_id or replay.request_hash != request_hash:
                raise api_error(status.HTTP_409_CONFLICT, "SYNC_IDEMPOTENCY_MISMATCH", "Operation ID content differs")
            return _resolution_record(replay, conflict_id)
        entity = session.scalar(
            select(SyncEntityRow).where(SyncEntityRow.id == conflict.entity_id, SyncEntityRow.owner_id == user.id).with_for_update()
        )
        if entity.version != payload.currentVersion or entity.content_hash != payload.currentContentHash:
            raise api_error(status.HTTP_409_CONFLICT, "SYNC_CONFLICT", "Entity changed after conflict review")
        source = conflict.local_snapshot
        target_entity = entity
        if payload.action == SyncResolutionAction.use_cloud:
            source = conflict.cloud_snapshot
        elif payload.action == SyncResolutionAction.manual_merge:
            source = {**conflict.local_snapshot, "payload": payload.payload, "tombstone": False}
        elif payload.action == SyncResolutionAction.restore_version:
            target = int((payload.payload or {}).get("version", 0))
            historic = session.scalar(
                select(SyncEntityVersionRow).where(
                    SyncEntityVersionRow.entity_id == entity.id,
                    SyncEntityVersionRow.owner_id == user.id,
                    SyncEntityVersionRow.version == target,
                )
            )
            if historic is None:
                raise api_error(status.HTTP_404_NOT_FOUND, "SYNC_ENTITY_NOT_FOUND", "Version was not found")
            source = {"payload": historic.payload, "tombstone": historic.tombstone}
        elif payload.action == SyncResolutionAction.undo:
            prior = session.scalar(
                select(SyncConflictResolutionRow).where(
                    SyncConflictResolutionRow.owner_id == user.id,
                    SyncConflictResolutionRow.public_id == payload.undoesResolutionId,
                    SyncConflictResolutionRow.conflict_id == conflict.id,
                )
            )
            if prior is None:
                raise api_error(status.HTTP_404_NOT_FOUND, "SYNC_ENTITY_NOT_FOUND", "Resolution was not found")
            latest = session.scalar(
                select(SyncConflictResolutionRow)
                .where(
                    SyncConflictResolutionRow.owner_id == user.id,
                    SyncConflictResolutionRow.conflict_id == conflict.id,
                )
                .order_by(SyncConflictResolutionRow.resolved_at.desc(), SyncConflictResolutionRow.id.desc())
                .limit(1)
            )
            if latest is None or latest.id != prior.id:
                raise api_error(status.HTTP_409_CONFLICT, "SYNC_CONFLICT", "Only the latest resolution can be undone")
            if prior.action == SyncResolutionAction.save_copy.value:
                target_entity = session.scalar(
                    select(SyncEntityRow)
                    .where(
                        SyncEntityRow.id == prior.accepted_version.entity_id,
                        SyncEntityRow.owner_id == user.id,
                    )
                    .with_for_update()
                )
                if (
                    target_entity is None
                    or target_entity.version != prior.accepted_version.version
                    or target_entity.content_hash != prior.accepted_version.content_hash
                ):
                    raise api_error(status.HTTP_409_CONFLICT, "SYNC_CONFLICT", "Saved copy changed after resolution")
                source = {"payload": None, "tombstone": True}
            else:
                before = session.scalar(
                    select(SyncEntityVersionRow).where(
                        SyncEntityVersionRow.entity_id == entity.id,
                        SyncEntityVersionRow.version == prior.accepted_version.version - 1,
                    )
                )
                if before is None:
                    raise api_error(
                        status.HTTP_409_CONFLICT,
                        "SYNC_CONFLICT",
                        "Resolution has no restorable predecessor",
                    )
                source = {"payload": before.payload, "tombstone": before.tombstone}
        now = now_utc()
        if payload.action == SyncResolutionAction.save_copy:
            copy_payload = source.get("payload")
            copy_tombstone = bool(source.get("tombstone"))
            copy_hash = _digest(copy_payload)
            copy = SyncEntityRow(
                owner_id=user.id,
                entity_type=entity.entity_type,
                public_id=payload.newEntityId,
                scope=entity.scope,
                schema_version=entity.schema_version,
                version=1,
                content_hash=copy_hash,
                payload=copy_payload,
                tombstone=copy_tombstone,
                created_at=now,
                updated_at=now,
                deleted_at=now if copy_tombstone else None,
            )
            session.add(copy)
            session.flush()
            version = SyncEntityVersionRow(
                entity_id=copy.id,
                owner_id=user.id,
                version=1,
                schema_version=copy.schema_version,
                content_hash=copy_hash,
                payload=copy_payload,
                tombstone=copy_tombstone,
                mutation_kind="create",
                operation_id=payload.operationId,
                base_version=None,
                base_hash=None,
                device_id=device_id,
                created_at=now,
            )
            session.add(version)
            session.flush()
            session.add(
                SyncChangeLogRow(
                    entity_version_id=version.id,
                    owner_id=user.id,
                    scope=copy.scope,
                    entity_type=copy.entity_type,
                    public_id=copy.public_id,
                    version=1,
                    content_hash=copy_hash,
                    mutation_kind="create",
                    tombstone=copy_tombstone,
                    operation_id=payload.operationId,
                    created_at=now,
                )
            )
        else:
            version = _append_version(
                session,
                entity=target_entity,
                payload=source.get("payload"),
                tombstone=bool(source.get("tombstone")),
                operation_id=payload.operationId,
                action=payload.action.value,
                device_id=device_id,
                now=now,
            )
        resolution = SyncConflictResolutionRow(
            public_id=str(uuid4()),
            conflict_id=conflict.id,
            owner_id=user.id,
            operation_id=payload.operationId,
            request_hash=request_hash,
            action=payload.action.value,
            actor_device_id=device_id,
            accepted_version_id=version.id,
            result_snapshot=_snapshot(
                schema_version=version.schema_version,
                version=version.version,
                content_hash=version.content_hash,
                mutation_kind=version.mutation_kind,
                tombstone=version.tombstone,
                payload=version.payload,
                device_id=device_id,
                modified_at=now,
            ),
            new_entity_public_id=payload.newEntityId,
            undoes_resolution_id=prior.id if payload.action == SyncResolutionAction.undo else None,
            resolved_at=now,
        )
        session.add(resolution)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            replay = session.scalar(
                select(SyncConflictResolutionRow).where(
                    SyncConflictResolutionRow.owner_id == user.id,
                    SyncConflictResolutionRow.operation_id == payload.operationId,
                )
            )
            if replay is None:
                raise
            if replay.conflict.public_id != conflict_id or replay.request_hash != request_hash:
                raise api_error(
                    status.HTTP_409_CONFLICT,
                    "SYNC_IDEMPOTENCY_MISMATCH",
                    "Operation ID content differs",
                ) from None
            return _resolution_record(replay, conflict_id)
        session.refresh(resolution)
        return _resolution_record(resolution, conflict_id)


def _resolution_record(row: SyncConflictResolutionRow, conflict_public_id: str) -> SyncConflictResolutionRecord:
    result = SyncConflictSnapshot.model_validate(row.result_snapshot)
    return SyncConflictResolutionRecord(
        protocolVersion=1,
        resolutionId=row.public_id,
        conflictId=conflict_public_id,
        operationId=row.operation_id,
        action=row.action,
        actorDeviceId=row.actor_device_id,
        acceptedVersion=result.version,
        acceptedContentHash=result.contentHash,
        result=result,
        newEntityId=row.new_entity_public_id,
        undoesResolutionId=row.undoes_resolution.public_id if row.undoes_resolution_id else None,
        resolvedAt=row.resolved_at,
    )


def restore_version(
    entity_type: SyncEntityType,
    entity_id: str,
    target_version: int,
    payload: SyncVersionRestoreRequest,
    *,
    user: UserEntity,
    device_id: str,
) -> SyncVersionRestoreRecord:
    entity_type = SyncEntityType(entity_type)
    request_hash = _digest(
        {
            "entityType": entity_type.value,
            "entityId": entity_id,
            "targetVersion": target_version,
            "request": payload.model_dump(mode="json"),
        }
    )
    with SessionLocal() as session:
        replay = session.scalar(
            select(SyncVersionRestoreRow).where(
                SyncVersionRestoreRow.owner_id == user.id,
                SyncVersionRestoreRow.operation_id == payload.operationId,
            )
        )
        if replay is not None:
            if replay.request_hash != request_hash:
                raise api_error(status.HTTP_409_CONFLICT, "SYNC_IDEMPOTENCY_MISMATCH", "Operation ID content differs")
            return _restore_record(replay)
        entity = session.scalar(
            select(SyncEntityRow)
            .where(
                SyncEntityRow.owner_id == user.id,
                SyncEntityRow.entity_type == entity_type.value,
                SyncEntityRow.public_id == entity_id,
            )
            .with_for_update()
        )
        if entity is None:
            raise api_error(status.HTTP_404_NOT_FOUND, "SYNC_ENTITY_NOT_FOUND", "Entity was not found")
        replay = session.scalar(
            select(SyncVersionRestoreRow).where(
                SyncVersionRestoreRow.owner_id == user.id,
                SyncVersionRestoreRow.operation_id == payload.operationId,
            )
        )
        if replay is not None:
            if replay.request_hash != request_hash:
                raise api_error(status.HTTP_409_CONFLICT, "SYNC_IDEMPOTENCY_MISMATCH", "Operation ID content differs")
            return _restore_record(replay)
        if entity.version != payload.currentVersion or entity.content_hash != payload.currentContentHash:
            raise api_error(status.HTTP_409_CONFLICT, "SYNC_CONFLICT", "Entity changed after history review")
        historic = session.scalar(
            select(SyncEntityVersionRow).where(
                SyncEntityVersionRow.owner_id == user.id,
                SyncEntityVersionRow.entity_id == entity.id,
                SyncEntityVersionRow.version == target_version,
            )
        )
        if historic is None:
            raise api_error(status.HTTP_404_NOT_FOUND, "SYNC_ENTITY_NOT_FOUND", "Version was not found")
        now = now_utc()
        accepted = _append_version(
            session,
            entity=entity,
            payload=historic.payload,
            tombstone=historic.tombstone,
            operation_id=payload.operationId,
            action="restoreVersion",
            device_id=device_id,
            now=now,
        )
        audit = SyncVersionRestoreRow(
            owner_id=user.id,
            entity_id=entity.id,
            operation_id=payload.operationId,
            request_hash=request_hash,
            target_version=target_version,
            accepted_version_id=accepted.id,
            actor_device_id=device_id,
            restored_at=now,
        )
        session.add(audit)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            replay = session.scalar(
                select(SyncVersionRestoreRow).where(
                    SyncVersionRestoreRow.owner_id == user.id,
                    SyncVersionRestoreRow.operation_id == payload.operationId,
                )
            )
            if replay is None:
                raise
            if replay.request_hash != request_hash:
                raise api_error(
                    status.HTTP_409_CONFLICT,
                    "SYNC_IDEMPOTENCY_MISMATCH",
                    "Operation ID content differs",
                ) from None
            return _restore_record(replay)
        session.refresh(audit)
        return _restore_record(audit)


def _restore_record(row: SyncVersionRestoreRow) -> SyncVersionRestoreRecord:
    entity = row.entity
    accepted = row.accepted_version
    return SyncVersionRestoreRecord(
        protocolVersion=1,
        operationId=row.operation_id,
        entityType=entity.entity_type,
        entityId=entity.public_id,
        restoredFromVersion=row.target_version,
        acceptedVersion=accepted.version,
        acceptedContentHash=accepted.content_hash,
        result=_version_record(accepted, entity_type=entity.entity_type, entity_id=entity.public_id),
        actorDeviceId=row.actor_device_id,
        restoredAt=row.restored_at,
    )
