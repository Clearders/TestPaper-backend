from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

STABLE_ID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
HASH_PATTERN = r"^[0-9a-f]{64}$"
StableId = Annotated[str, Field(pattern=STABLE_ID_PATTERN)]
ContentHash = Annotated[str, Field(pattern=HASH_PATTERN)]


class SyncEntityType(StrEnum):
    question = "question"
    paper = "paper"
    draft = "draft"
    attachment = "attachment"
    comment = "comment"
    favorite = "favorite"
    setting = "setting"


class SyncMutationKind(StrEnum):
    create = "create"
    update = "update"
    delete = "delete"
    restore = "restore"
    rename = "rename"
    attach = "attach"
    detach = "detach"


class SyncOperationStatus(StrEnum):
    applied = "applied"
    noop = "noop"
    conflict = "conflict"
    rejected = "rejected"
    dependency_failed = "dependencyFailed"


class SyncErrorCode(StrEnum):
    protocol_unsupported = "SYNC_PROTOCOL_UNSUPPORTED"
    batch_invalid = "SYNC_BATCH_INVALID"
    batch_too_large = "SYNC_BATCH_TOO_LARGE"
    idempotency_mismatch = "SYNC_IDEMPOTENCY_MISMATCH"
    dependency_failed = "SYNC_DEPENDENCY_FAILED"
    conflict = "SYNC_CONFLICT"
    cursor_invalid = "SYNC_CURSOR_INVALID"
    cursor_expired = "SYNC_CURSOR_EXPIRED"
    snapshot_expired = "SYNC_SNAPSHOT_EXPIRED"
    entity_forbidden = "SYNC_ENTITY_FORBIDDEN"
    entity_not_found = "SYNC_ENTITY_NOT_FOUND"
    entity_schema_unsupported = "SYNC_ENTITY_SCHEMA_UNSUPPORTED"
    upload_expired = "SYNC_UPLOAD_EXPIRED"
    upload_chunk_mismatch = "SYNC_UPLOAD_CHUNK_MISMATCH"
    upload_incomplete = "SYNC_UPLOAD_INCOMPLETE"
    attachment_hash_mismatch = "SYNC_ATTACHMENT_HASH_MISMATCH"


class SyncMutation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operationId: StableId
    entityType: SyncEntityType
    entityId: StableId
    kind: SyncMutationKind
    baseVersion: int | None = Field(default=None, ge=1)
    baseContentHash: ContentHash | None = None
    payload: dict[str, Any] | None = None
    dependsOn: list[StableId] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_preconditions(self):
        if len(set(self.dependsOn)) != len(self.dependsOn):
            raise ValueError("dependsOn values must be unique")
        if self.operationId in self.dependsOn:
            raise ValueError("an operation cannot depend on itself")
        if self.kind == SyncMutationKind.create:
            if self.baseVersion is not None or self.baseContentHash is not None:
                raise ValueError("create must not include baseVersion or baseContentHash")
        elif self.baseVersion is None or self.baseContentHash is None:
            raise ValueError("non-create mutations require baseVersion and baseContentHash")
        if self.kind == SyncMutationKind.delete:
            if self.payload is not None:
                raise ValueError("delete payload must be null")
        elif self.payload is None:
            raise ValueError("non-delete mutations require payload")
        return self


class SyncPushRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocolVersion: int
    batchId: StableId
    deviceId: str = Field(min_length=1, max_length=128)
    mutations: list[SyncMutation] = Field(max_length=100)

    @model_validator(mode="after")
    def validate_operation_graph(self):
        operation_ids = [mutation.operationId for mutation in self.mutations]
        if len(set(operation_ids)) != len(operation_ids):
            raise ValueError("operationId values must be unique within a batch")
        seen: set[str] = set()
        for mutation in self.mutations:
            unknown = set(mutation.dependsOn) - seen
            if unknown:
                raise ValueError(f"dependencies must refer to earlier operations: {sorted(unknown)}")
            seen.add(mutation.operationId)
        return self


class SyncError(BaseModel):
    code: SyncErrorCode
    message: str
    retryable: bool
    details: dict[str, Any] | None = None


class SyncOperationResult(BaseModel):
    operationId: StableId
    status: SyncOperationStatus
    entityVersion: int | None = None
    contentHash: ContentHash | None = None
    changeCursor: str | None = None
    conflictId: StableId | None = None
    failedDependencyIds: list[StableId] | None = None
    error: SyncError | None = None


class SyncPushResponse(BaseModel):
    protocolVersion: int
    batchId: StableId
    results: list[SyncOperationResult]


class SyncChange(BaseModel):
    sequence: str = Field(pattern=r"^[0-9]+$")
    entityType: SyncEntityType
    entityId: StableId
    kind: SyncMutationKind
    version: int = Field(ge=1)
    contentHash: ContentHash
    updatedAt: datetime
    snapshot: dict[str, Any] | None = None


class SyncPullResponse(BaseModel):
    protocolVersion: int
    changes: list[SyncChange]
    nextCursor: str = Field(min_length=1)
    hasMore: bool


class SyncAckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocolVersion: int
    deviceId: str = Field(min_length=1, max_length=128)
    cursor: str = Field(min_length=1)


class SyncAckResponse(BaseModel):
    protocolVersion: int
    deviceId: str
    cursor: str
    advanced: bool


class SyncSnapshotResponse(BaseModel):
    protocolVersion: int
    snapshotId: StableId
    entries: list[SyncChange]
    nextCursor: str = Field(min_length=1)
    hasMore: bool
    resumeCursor: str = Field(min_length=1)


class AttachmentUploadInitiateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocolVersion: int
    idempotencyKey: str = Field(min_length=1, max_length=128)
    attachmentId: StableId
    targetEntityId: StableId
    contentHash: ContentHash
    byteSize: int = Field(gt=0, le=100 * 1024 * 1024)
    chunkSize: int = Field(default=1024 * 1024, ge=256 * 1024, le=8 * 1024 * 1024)
    fileName: str = Field(min_length=1, max_length=255, pattern=r"^[^\x00-\x1f\x7f/\\]+$")
    contentType: str = Field(
        min_length=3,
        max_length=255,
        pattern=r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+$",
    )


class AttachmentUploadCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocolVersion: int


class AttachmentUploadStatus(BaseModel):
    protocolVersion: int
    uploadId: StableId
    attachmentId: StableId
    deduplicated: bool
    completed: bool
    chunkSize: int
    totalChunks: int
    uploadedBytes: int
    missingChunks: list[int]
    expiresAt: datetime
    contentHash: ContentHash
    byteSize: int


class AttachmentChunkReceipt(BaseModel):
    protocolVersion: int
    uploadId: StableId
    ordinal: int
    duplicate: bool
    uploadedBytes: int
    missingChunks: list[int]
