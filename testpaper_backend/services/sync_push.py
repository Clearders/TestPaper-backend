from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any

from fastapi import status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from testpaper_backend.core.errors import api_error
from testpaper_backend.db import (
    SessionLocal,
    SyncChangeLogRow,
    SyncEntityRow,
    SyncEntityVersionRow,
    SyncIdempotencyBatchRow,
    SyncOperationResultRow,
)
from testpaper_backend.schemas import (
    SyncEntityType,
    SyncError,
    SyncMutation,
    SyncMutationKind,
    SyncOperationResult,
    SyncOperationStatus,
    SyncPushRequest,
    SyncPushResponse,
    UserEntity,
)
from testpaper_backend.schemas.sync import (
    MAX_SYNC_BATCH_BYTES,
    MAX_SYNC_MUTATION_BYTES,
    MAX_SYNC_MUTATIONS,
    SYNC_IDEMPOTENCY_RETENTION_DAYS,
    SYNC_PROTOCOL_VERSION,
)
from testpaper_backend.services.attachment_maintenance import apply_attachment_reference_lifecycle
from testpaper_backend.services.sync_conflicts import create_conflict
from testpaper_backend.time_utils import now_utc

IDEMPOTENCY_RETENTION_DAYS = SYNC_IDEMPOTENCY_RETENTION_DAYS
MAX_PUSH_MUTATIONS = MAX_SYNC_MUTATIONS
MAX_PUSH_MUTATION_BYTES = MAX_SYNC_MUTATION_BYTES
MAX_PUSH_BATCH_BYTES = MAX_SYNC_BATCH_BYTES

_DB_STATUS = {
    SyncOperationStatus.applied: "applied",
    SyncOperationStatus.noop: "noop",
    SyncOperationStatus.conflict: "conflict",
    SyncOperationStatus.rejected: "rejected",
    SyncOperationStatus.dependency_failed: "dependency_failed",
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


def _request_hash(payload: SyncPushRequest) -> str:
    return _digest(payload.model_dump(mode="json"))


def _canonical_size(value: Any) -> int:
    return len(_canonical_json(value).encode("utf-8"))


def _batch_limit_details() -> dict[str, int]:
    return {
        "maxMutations": MAX_PUSH_MUTATIONS,
        "maxMutationBytes": MAX_PUSH_MUTATION_BYTES,
        "maxBatchBytes": MAX_PUSH_BATCH_BYTES,
    }


def _enforce_batch_limits(payload: SyncPushRequest) -> None:
    details = _batch_limit_details()
    if len(payload.mutations) > MAX_PUSH_MUTATIONS:
        raise api_error(
            status.HTTP_413_CONTENT_TOO_LARGE,
            "SYNC_BATCH_TOO_LARGE",
            f"A push batch may contain at most {MAX_PUSH_MUTATIONS} mutations",
            details,
        )
    for mutation in payload.mutations:
        if _canonical_size(mutation.model_dump(mode="json")) > MAX_PUSH_MUTATION_BYTES:
            raise api_error(
                status.HTTP_413_CONTENT_TOO_LARGE,
                "SYNC_BATCH_TOO_LARGE",
                "A sync mutation exceeds the canonical JSON byte limit",
                details,
            )
    if _canonical_size(payload.model_dump(mode="json")) > MAX_PUSH_BATCH_BYTES:
        raise api_error(
            status.HTTP_413_CONTENT_TOO_LARGE,
            "SYNC_BATCH_TOO_LARGE",
            "The sync batch exceeds the canonical JSON byte limit",
            details,
        )


def _error(code: str, message: str, *, details: dict[str, Any] | None = None) -> SyncError:
    return SyncError(code=code, message=message, retryable=False, details=details)


def _rejected(mutation: SyncMutation, code: str, message: str, *, details: dict[str, Any] | None = None) -> SyncOperationResult:
    return SyncOperationResult(
        operationId=mutation.operationId,
        status=SyncOperationStatus.rejected,
        error=_error(code, message, details=details),
    )


def _conflict(mutation: SyncMutation, entity: SyncEntityRow) -> SyncOperationResult:
    return SyncOperationResult(
        operationId=mutation.operationId,
        status=SyncOperationStatus.conflict,
        entityVersion=entity.version,
        contentHash=entity.content_hash,
        error=_error(
            "SYNC_CONFLICT",
            "The entity changed since the supplied base version",
            details={
                "currentVersion": entity.version,
                "currentContentHash": entity.content_hash,
                "tombstone": entity.tombstone,
            },
        ),
    )


def _can_mutate(user: UserEntity, entity_type: SyncEntityType) -> bool:
    permissions = set(user.permissions)
    if entity_type == SyncEntityType.question:
        return "questions:write" in permissions
    if entity_type in {
        SyncEntityType.paper,
        SyncEntityType.draft,
        SyncEntityType.attachment,
        SyncEntityType.comment,
    }:
        return "papers:write" in permissions
    return entity_type in {SyncEntityType.favorite, SyncEntityType.setting}


def _entity_query(owner_id: int, mutation: SyncMutation):
    return (
        select(SyncEntityRow)
        .where(
            SyncEntityRow.owner_id == owner_id,
            SyncEntityRow.entity_type == mutation.entityType.value,
            SyncEntityRow.public_id == mutation.entityId,
        )
        .with_for_update()
    )


def _apply_mutation(
    session: Session,
    *,
    user: UserEntity,
    device_id: str,
    mutation: SyncMutation,
) -> SyncOperationResult:
    if not _can_mutate(user, mutation.entityType):
        return _rejected(mutation, "SYNC_ENTITY_FORBIDDEN", "The authenticated account cannot mutate this entity type")

    reused = session.scalar(
        select(SyncOperationResultRow).where(
            SyncOperationResultRow.owner_id == user.id,
            SyncOperationResultRow.operation_id == mutation.operationId,
        )
    )
    if reused is not None:
        return _rejected(
            mutation,
            "SYNC_BATCH_INVALID",
            "operationId was already submitted in a different batch",
        )

    entity = session.scalar(_entity_query(user.id, mutation))
    desired_payload = None if mutation.kind == SyncMutationKind.delete else mutation.payload
    schema_version = desired_payload.get("schemaVersion", 1) if desired_payload is not None else 1
    if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version != 1:
        return _rejected(
            mutation,
            "SYNC_ENTITY_SCHEMA_UNSUPPORTED",
            "The entity schema version is unsupported",
            details={"supportedSchemaVersions": [1]},
        )
    desired_hash = _digest(desired_payload)
    now = now_utc()

    if mutation.kind == SyncMutationKind.create:
        if entity is not None:
            if not entity.tombstone and entity.content_hash == desired_hash:
                return SyncOperationResult(
                    operationId=mutation.operationId,
                    status=SyncOperationStatus.noop,
                    entityVersion=entity.version,
                    contentHash=entity.content_hash,
                )
            return create_conflict(session, user=user, device_id=device_id, mutation=mutation, entity=entity)
        entity = SyncEntityRow(
            owner_id=user.id,
            entity_type=mutation.entityType.value,
            public_id=mutation.entityId,
            scope="personal",
            schema_version=schema_version,
            version=1,
            content_hash=desired_hash,
            payload=desired_payload,
            tombstone=False,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        session.add(entity)
        session.flush()
        next_version = 1
    else:
        if entity is None:
            return _rejected(mutation, "SYNC_ENTITY_NOT_FOUND", "The entity does not exist")
        if entity.version != mutation.baseVersion or entity.content_hash != mutation.baseContentHash:
            return create_conflict(session, user=user, device_id=device_id, mutation=mutation, entity=entity)
        if mutation.kind == SyncMutationKind.restore:
            if not entity.tombstone:
                return create_conflict(session, user=user, device_id=device_id, mutation=mutation, entity=entity)
        elif entity.tombstone:
            return create_conflict(session, user=user, device_id=device_id, mutation=mutation, entity=entity)
        if mutation.kind != SyncMutationKind.delete and entity.content_hash == desired_hash:
            return SyncOperationResult(
                operationId=mutation.operationId,
                status=SyncOperationStatus.noop,
                entityVersion=entity.version,
                contentHash=entity.content_hash,
            )
        next_version = entity.version + 1
        entity.version = next_version
        entity.schema_version = schema_version
        entity.content_hash = desired_hash
        entity.payload = desired_payload
        entity.tombstone = mutation.kind == SyncMutationKind.delete
        entity.deleted_at = now if entity.tombstone else None
        entity.updated_at = now

    apply_attachment_reference_lifecycle(
        session,
        entity=entity,
        mutation_kind=mutation.kind.value,
        occurred_at=now,
    )

    version = SyncEntityVersionRow(
        entity_id=entity.id,
        owner_id=user.id,
        version=next_version,
        schema_version=schema_version,
        content_hash=desired_hash,
        payload=desired_payload,
        tombstone=entity.tombstone,
        mutation_kind=mutation.kind.value,
        operation_id=mutation.operationId,
        base_version=mutation.baseVersion,
        base_hash=mutation.baseContentHash,
        device_id=device_id,
        created_at=now,
    )
    session.add(version)
    session.flush()
    session.add(
        SyncChangeLogRow(
            entity_version_id=version.id,
            owner_id=user.id,
            scope=entity.scope,
            entity_type=entity.entity_type,
            public_id=entity.public_id,
            version=next_version,
            content_hash=desired_hash,
            mutation_kind=mutation.kind.value,
            tombstone=entity.tombstone,
            operation_id=mutation.operationId,
            created_at=now,
        )
    )
    session.flush()
    return SyncOperationResult(
        operationId=mutation.operationId,
        status=SyncOperationStatus.applied,
        entityVersion=next_version,
        contentHash=desired_hash,
    )


def _load_batch(session: Session, *, owner_id: int, device_id: str, batch_id: str) -> SyncIdempotencyBatchRow | None:
    return session.scalar(
        select(SyncIdempotencyBatchRow)
        .where(
            SyncIdempotencyBatchRow.owner_id == owner_id,
            SyncIdempotencyBatchRow.device_id == device_id,
            SyncIdempotencyBatchRow.idempotency_key == batch_id,
        )
        .with_for_update()
    )


def _replay_batch(session: Session, batch: SyncIdempotencyBatchRow, request_hash: str) -> SyncPushResponse:
    if batch.request_hash != request_hash:
        raise api_error(
            status.HTTP_409_CONFLICT,
            "SYNC_IDEMPOTENCY_MISMATCH",
            "The batch ID was previously used with different content",
        )
    if batch.response_payload is None:
        raise api_error(status.HTTP_409_CONFLICT, "SYNC_BATCH_INVALID", "The original batch has not settled")
    now = now_utc()
    batch.last_replayed_at = now
    batch.expires_at = now + timedelta(days=IDEMPOTENCY_RETENTION_DAYS)
    response = SyncPushResponse.model_validate(batch.response_payload)
    session.commit()
    return response


def _result_row(
    *,
    batch_id: int,
    owner_id: int,
    ordinal: int,
    mutation: SyncMutation,
    result: SyncOperationResult,
) -> SyncOperationResultRow:
    return SyncOperationResultRow(
        batch_id=batch_id,
        owner_id=owner_id,
        ordinal=ordinal,
        operation_id=mutation.operationId,
        status=_DB_STATUS[result.status],
        entity_type=mutation.entityType.value,
        public_id=mutation.entityId,
        accepted_version=result.entityVersion,
        content_hash=result.contentHash,
        error_code=result.error.code if result.error else None,
        details=result.model_dump(mode="json", exclude_none=True),
    )


def push_mutations(
    payload: SyncPushRequest,
    *,
    user: UserEntity,
    authenticated_device_id: str,
    request_id: str,
) -> SyncPushResponse:
    if payload.protocolVersion != SYNC_PROTOCOL_VERSION:
        raise api_error(
            status.HTTP_426_UPGRADE_REQUIRED,
            "SYNC_PROTOCOL_UNSUPPORTED",
            f"Sync protocol {payload.protocolVersion} is unsupported",
            {"supportedVersions": [SYNC_PROTOCOL_VERSION]},
        )
    if payload.deviceId != authenticated_device_id:
        raise api_error(status.HTTP_403_FORBIDDEN, "SYNC_ENTITY_FORBIDDEN", "deviceId does not match the access token")
    _enforce_batch_limits(payload)

    request_hash = _request_hash(payload)
    now = now_utc()
    with SessionLocal() as session:
        existing = _load_batch(session, owner_id=user.id, device_id=authenticated_device_id, batch_id=payload.batchId)
        if existing is not None:
            return _replay_batch(session, existing, request_hash)

        batch = SyncIdempotencyBatchRow(
            owner_id=user.id,
            device_id=authenticated_device_id,
            idempotency_key=payload.batchId,
            request_hash=request_hash,
            protocol_version=SYNC_PROTOCOL_VERSION,
            status="processing",
            request_id=request_id,
            response_status=None,
            response_payload=None,
            expires_at=now + timedelta(days=IDEMPOTENCY_RETENTION_DAYS),
            created_at=now,
            last_replayed_at=now,
            completed_at=None,
        )
        session.add(batch)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            existing = _load_batch(session, owner_id=user.id, device_id=authenticated_device_id, batch_id=payload.batchId)
            if existing is None:
                raise
            return _replay_batch(session, existing, request_hash)

        results: list[SyncOperationResult] = []
        results_by_id: dict[str, SyncOperationResult] = {}
        for ordinal, mutation in enumerate(payload.mutations):
            try:
                with session.begin_nested():
                    failed_dependencies = [
                        dependency
                        for dependency in mutation.dependsOn
                        if results_by_id[dependency].status not in {SyncOperationStatus.applied, SyncOperationStatus.noop}
                    ]
                    if failed_dependencies:
                        result = SyncOperationResult(
                            operationId=mutation.operationId,
                            status=SyncOperationStatus.dependency_failed,
                            failedDependencyIds=failed_dependencies,
                            error=_error("SYNC_DEPENDENCY_FAILED", "A declared dependency did not apply"),
                        )
                    else:
                        result = _apply_mutation(
                            session,
                            user=user,
                            device_id=authenticated_device_id,
                            mutation=mutation,
                        )
                    session.add(
                        _result_row(
                            batch_id=batch.id,
                            owner_id=user.id,
                            ordinal=ordinal,
                            mutation=mutation,
                            result=result,
                        )
                    )
                    session.flush()
            except IntegrityError:
                reused = session.scalar(
                    select(SyncOperationResultRow).where(
                        SyncOperationResultRow.owner_id == user.id,
                        SyncOperationResultRow.operation_id == mutation.operationId,
                    )
                )
                if reused is not None:
                    result = _rejected(
                        mutation,
                        "SYNC_BATCH_INVALID",
                        "operationId was concurrently submitted in a different batch",
                    )
                else:
                    current = session.scalar(_entity_query(user.id, mutation))
                    if mutation.kind == SyncMutationKind.create and current is not None:
                        with session.begin_nested():
                            result = create_conflict(
                                session,
                                user=user,
                                device_id=authenticated_device_id,
                                mutation=mutation,
                                entity=current,
                            )
                            session.add(
                                _result_row(
                                    batch_id=batch.id,
                                    owner_id=user.id,
                                    ordinal=ordinal,
                                    mutation=mutation,
                                    result=result,
                                )
                            )
                            session.flush()
                    else:
                        result = (
                            _conflict(mutation, current)
                            if current is not None
                            else _rejected(
                                mutation,
                                "SYNC_BATCH_INVALID",
                                "The operation violates a sync persistence invariant",
                            )
                        )
                        with session.begin_nested():
                            session.add(
                                _result_row(
                                    batch_id=batch.id,
                                    owner_id=user.id,
                                    ordinal=ordinal,
                                    mutation=mutation,
                                    result=result,
                                )
                            )
                            session.flush()
            results.append(result)
            results_by_id[mutation.operationId] = result

        response = SyncPushResponse(protocolVersion=SYNC_PROTOCOL_VERSION, batchId=payload.batchId, results=results)
        batch.status = "completed"
        batch.response_status = status.HTTP_200_OK
        batch.response_payload = response.model_dump(mode="json")
        batch.completed_at = now_utc()
        session.commit()
        return response
