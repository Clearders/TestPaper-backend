from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

from fastapi import status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from testpaper_backend.core.errors import api_error
from testpaper_backend.db import (
    AttachmentBlobRow,
    AttachmentReferenceRow,
    AttachmentUploadChunkRow,
    AttachmentUploadSessionRow,
    SessionLocal,
    SyncEntityRow,
)
from testpaper_backend.schemas import (
    AttachmentChunkReceipt,
    AttachmentUploadInitiateRequest,
    AttachmentUploadStatus,
    UserEntity,
)
from testpaper_backend.services.attachment_access import require_attachment_download
from testpaper_backend.services.attachment_storage import AttachmentStorageError, FilesystemAttachmentStorage
from testpaper_backend.time_utils import as_aware_utc, now_utc

SYNC_PROTOCOL_VERSION = 1
UPLOAD_TTL_HOURS = 24
_HASH = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class AttachmentDownload:
    content: bytes
    file_name: str
    content_type: str
    content_hash: str


def _protocol(version: int) -> None:
    if version != SYNC_PROTOCOL_VERSION:
        raise api_error(status.HTTP_426_UPGRADE_REQUIRED, "SYNC_PROTOCOL_UNSUPPORTED", "Sync protocol version is unsupported")


def _request_hash(payload: AttachmentUploadInitiateRequest) -> str:
    canonical = json.dumps(payload.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(canonical).hexdigest()


def _missing_chunks(session: Session, upload: AttachmentUploadSessionRow) -> list[int]:
    received = set(session.scalars(select(AttachmentUploadChunkRow.ordinal).where(AttachmentUploadChunkRow.session_id == upload.id)).all())
    return [ordinal for ordinal in range(upload.total_chunks) if ordinal not in received]


def _upload_status(session: Session, upload: AttachmentUploadSessionRow) -> AttachmentUploadStatus:
    reference = session.get(AttachmentReferenceRow, upload.reference_id)
    if reference is None:
        raise api_error(status.HTTP_404_NOT_FOUND, "SYNC_ENTITY_NOT_FOUND", "Attachment not found")
    missing = _missing_chunks(session, upload)
    return AttachmentUploadStatus(
        protocolVersion=SYNC_PROTOCOL_VERSION,
        uploadId=upload.public_id,
        attachmentId=reference.public_id,
        deduplicated=upload.status == "completed" and len(missing) == upload.total_chunks,
        completed=upload.status == "completed",
        chunkSize=upload.chunk_size,
        totalChunks=upload.total_chunks,
        uploadedBytes=upload.uploaded_bytes,
        missingChunks=missing,
        expiresAt=upload.expires_at,
        contentHash=upload.content_hash,
        byteSize=upload.byte_size,
    )


def _owned_entity(session: Session, *, owner_id: int, public_id: str, entity_type: str | None = None) -> SyncEntityRow:
    conditions = [
        SyncEntityRow.owner_id == owner_id,
        SyncEntityRow.public_id == public_id,
        SyncEntityRow.scope == "personal",
        SyncEntityRow.tombstone.is_(False),
    ]
    if entity_type is not None:
        conditions.append(SyncEntityRow.entity_type == entity_type)
    row = session.scalar(select(SyncEntityRow).where(*conditions))
    if row is None:
        raise api_error(status.HTTP_404_NOT_FOUND, "SYNC_ENTITY_NOT_FOUND", "Attachment target not found")
    return row


def _reference(
    session: Session,
    *,
    payload: AttachmentUploadInitiateRequest,
    owner_id: int,
    attachment: SyncEntityRow,
    target: SyncEntityRow,
) -> AttachmentReferenceRow:
    now = now_utc()
    session.execute(
        pg_insert(AttachmentReferenceRow)
        .values(
            publicId=payload.attachmentId,
            ownerId=owner_id,
            scope="personal",
            attachmentEntityId=attachment.id,
            targetEntityId=target.id,
            blobId=None,
            contentHash=payload.contentHash,
            byteSize=payload.byteSize,
            fileName=payload.fileName,
            contentType=payload.contentType,
            availability="pending",
            tombstone=False,
            createdAt=now,
            updatedAt=now,
            deletedAt=None,
            retentionUntil=None,
        )
        .on_conflict_do_nothing(constraint="uq_attachment_references_owner_public_id")
    )
    reference = session.scalar(
        select(AttachmentReferenceRow)
        .where(AttachmentReferenceRow.owner_id == owner_id, AttachmentReferenceRow.public_id == payload.attachmentId)
        .with_for_update()
    )
    if reference is None:
        raise api_error(status.HTTP_409_CONFLICT, "SYNC_CONFLICT", "Attachment reference could not be created")
    expected = (
        attachment.id,
        target.id,
        payload.contentHash,
        payload.byteSize,
        payload.fileName,
        payload.contentType,
    )
    actual = (
        reference.attachment_entity_id,
        reference.target_entity_id,
        reference.content_hash,
        reference.byte_size,
        reference.file_name,
        reference.content_type,
    )
    if reference.tombstone or actual != expected:
        raise api_error(status.HTTP_409_CONFLICT, "SYNC_CONFLICT", "Attachment metadata differs from its existing identity")
    return reference


def _initiate_attachment_upload(
    payload: AttachmentUploadInitiateRequest,
    *,
    user: UserEntity,
    device_id: str,
    storage: FilesystemAttachmentStorage | None = None,
) -> AttachmentUploadStatus:
    _protocol(payload.protocolVersion)
    storage = storage or FilesystemAttachmentStorage()
    request_hash = _request_hash(payload)
    with SessionLocal() as session, session.begin():
        existing = session.scalar(
            select(AttachmentUploadSessionRow)
            .where(
                AttachmentUploadSessionRow.owner_id == user.id,
                AttachmentUploadSessionRow.device_id == device_id,
                AttachmentUploadSessionRow.idempotency_key == payload.idempotencyKey,
            )
            .with_for_update()
        )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise api_error(status.HTTP_409_CONFLICT, "SYNC_IDEMPOTENCY_MISMATCH", "Idempotency key content differs")
            if existing.status != "completed" and as_aware_utc(existing.expires_at) <= now_utc():
                raise api_error(status.HTTP_410_GONE, "SYNC_UPLOAD_EXPIRED", "Attachment upload has expired")
            return _upload_status(session, existing)

        attachment = _owned_entity(session, owner_id=user.id, public_id=payload.attachmentId, entity_type="attachment")
        target = _owned_entity(session, owner_id=user.id, public_id=payload.targetEntityId)
        reference = _reference(
            session,
            payload=payload,
            owner_id=user.id,
            attachment=attachment,
            target=target,
        )
        now = now_utc()
        storage_key = storage.blob_key(payload.contentHash)
        session.execute(
            pg_insert(AttachmentBlobRow)
            .values(
                sha256=payload.contentHash,
                byteSize=payload.byteSize,
                contentType=payload.contentType,
                storageKey=storage_key,
                status="pending",
                referenceCount=0,
                verifiedAt=None,
                gcEligibleAt=None,
                createdAt=now,
                updatedAt=now,
            )
            .on_conflict_do_nothing(constraint="uq_attachment_blobs_sha256")
        )
        blob = session.scalar(select(AttachmentBlobRow).where(AttachmentBlobRow.sha256 == payload.contentHash).with_for_update())
        if blob is None or blob.byte_size != payload.byteSize:
            raise api_error(status.HTTP_422_UNPROCESSABLE_ENTITY, "SYNC_ATTACHMENT_HASH_MISMATCH", "Attachment digest metadata differs")
        deduplicated = blob.status == "available" and storage.verify(
            blob.storage_key,
            content_hash=blob.sha256,
            byte_size=blob.byte_size,
        )
        if blob.status == "available" and not deduplicated:
            blob.status = "quarantined"
        upload_id = str(uuid4())
        total_chunks = math.ceil(payload.byteSize / payload.chunkSize)
        upload = AttachmentUploadSessionRow(
            public_id=upload_id,
            owner_id=user.id,
            device_id=device_id,
            reference_id=reference.id,
            idempotency_key=payload.idempotencyKey,
            request_hash=request_hash,
            content_hash=payload.contentHash,
            byte_size=payload.byteSize,
            chunk_size=payload.chunkSize,
            total_chunks=total_chunks,
            uploaded_bytes=payload.byteSize if deduplicated else 0,
            storage_prefix=f"uploads/{upload_id}",
            status="completed" if deduplicated else "initiated",
            blob_id=blob.id if deduplicated else None,
            expires_at=now + timedelta(hours=UPLOAD_TTL_HOURS),
            created_at=now,
            updated_at=now,
            completed_at=now if deduplicated else None,
        )
        session.add(upload)
        session.flush()
        if deduplicated:
            reference.blob_id = blob.id
            reference.availability = "available"
            reference.updated_at = now
            session.flush()
        return _upload_status(session, upload)


def initiate_attachment_upload(
    payload: AttachmentUploadInitiateRequest,
    *,
    user: UserEntity,
    device_id: str,
    storage: FilesystemAttachmentStorage | None = None,
) -> AttachmentUploadStatus:
    storage = storage or FilesystemAttachmentStorage()
    try:
        return _initiate_attachment_upload(payload, user=user, device_id=device_id, storage=storage)
    except IntegrityError:
        # Two identical requests can both miss the initial lookup. The database
        # serializes the unique key; the loser must return the winner's receipt.
        request_hash = _request_hash(payload)
        with SessionLocal() as session:
            existing = session.scalar(
                select(AttachmentUploadSessionRow).where(
                    AttachmentUploadSessionRow.owner_id == user.id,
                    AttachmentUploadSessionRow.device_id == device_id,
                    AttachmentUploadSessionRow.idempotency_key == payload.idempotencyKey,
                )
            )
            if existing is None:
                raise
            if existing.request_hash != request_hash:
                raise api_error(
                    status.HTTP_409_CONFLICT,
                    "SYNC_IDEMPOTENCY_MISMATCH",
                    "Idempotency key content differs",
                ) from None
            return _upload_status(session, existing)


def get_attachment_upload_status(*, upload_id: str, user: UserEntity, device_id: str) -> AttachmentUploadStatus:
    with SessionLocal() as session:
        upload = session.scalar(
            select(AttachmentUploadSessionRow).where(
                AttachmentUploadSessionRow.owner_id == user.id,
                AttachmentUploadSessionRow.device_id == device_id,
                AttachmentUploadSessionRow.public_id == upload_id,
            )
        )
        if upload is None:
            raise api_error(status.HTTP_404_NOT_FOUND, "SYNC_ENTITY_NOT_FOUND", "Attachment upload not found")
        if upload.status != "completed" and as_aware_utc(upload.expires_at) <= now_utc():
            raise api_error(status.HTTP_410_GONE, "SYNC_UPLOAD_EXPIRED", "Attachment upload has expired")
        return _upload_status(session, upload)


def upload_attachment_chunk(
    *,
    upload_id: str,
    ordinal: int,
    data: bytes,
    content_hash: str,
    user: UserEntity,
    device_id: str,
    storage: FilesystemAttachmentStorage | None = None,
) -> AttachmentChunkReceipt:
    storage = storage or FilesystemAttachmentStorage()
    if len(data) > 8 * 1024 * 1024:
        raise api_error(status.HTTP_413_CONTENT_TOO_LARGE, "SYNC_UPLOAD_CHUNK_MISMATCH", "Attachment chunk is too large")
    if not _HASH.fullmatch(content_hash) or hashlib.sha256(data).hexdigest() != content_hash:
        raise api_error(status.HTTP_409_CONFLICT, "SYNC_UPLOAD_CHUNK_MISMATCH", "Attachment chunk digest differs")
    with SessionLocal() as session, session.begin():
        upload = session.scalar(
            select(AttachmentUploadSessionRow)
            .where(
                AttachmentUploadSessionRow.owner_id == user.id,
                AttachmentUploadSessionRow.device_id == device_id,
                AttachmentUploadSessionRow.public_id == upload_id,
            )
            .with_for_update()
        )
        if upload is None:
            raise api_error(status.HTTP_404_NOT_FOUND, "SYNC_ENTITY_NOT_FOUND", "Attachment upload not found")
        if upload.status != "completed" and as_aware_utc(upload.expires_at) <= now_utc():
            raise api_error(status.HTTP_410_GONE, "SYNC_UPLOAD_EXPIRED", "Attachment upload has expired")
        if ordinal < 0 or ordinal >= upload.total_chunks:
            raise api_error(status.HTTP_422_UNPROCESSABLE_ENTITY, "SYNC_UPLOAD_CHUNK_MISMATCH", "Chunk ordinal is out of range")
        offset = ordinal * upload.chunk_size
        expected_size = min(upload.chunk_size, upload.byte_size - offset)
        if len(data) != expected_size:
            raise api_error(status.HTTP_409_CONFLICT, "SYNC_UPLOAD_CHUNK_MISMATCH", "Attachment chunk size differs")
        existing = session.get(AttachmentUploadChunkRow, (upload.id, ordinal))
        if existing is not None:
            if existing.sha256 != content_hash or existing.byte_size != len(data) or existing.byte_offset != offset:
                raise api_error(status.HTTP_409_CONFLICT, "SYNC_UPLOAD_CHUNK_MISMATCH", "Attachment chunk replay differs")
            return AttachmentChunkReceipt(
                protocolVersion=SYNC_PROTOCOL_VERSION,
                uploadId=upload.public_id,
                ordinal=ordinal,
                duplicate=True,
                uploadedBytes=upload.uploaded_bytes,
                missingChunks=_missing_chunks(session, upload),
            )
        if upload.status == "completed":
            raise api_error(status.HTTP_409_CONFLICT, "SYNC_UPLOAD_CHUNK_MISMATCH", "Completed upload has no such chunk")
        key = storage.chunk_key(upload.public_id, ordinal)
        try:
            storage.write_chunk(key, data, content_hash)
        except AttachmentStorageError as error:
            raise api_error(status.HTTP_409_CONFLICT, "SYNC_UPLOAD_CHUNK_MISMATCH", str(error)) from error
        now = now_utc()
        session.add(
            AttachmentUploadChunkRow(
                session_id=upload.id,
                ordinal=ordinal,
                byte_offset=offset,
                byte_size=len(data),
                sha256=content_hash,
                storage_key=key,
                verified_at=now,
                created_at=now,
            )
        )
        upload.uploaded_bytes += len(data)
        upload.status = "uploading"
        upload.updated_at = now
        session.flush()
        return AttachmentChunkReceipt(
            protocolVersion=SYNC_PROTOCOL_VERSION,
            uploadId=upload.public_id,
            ordinal=ordinal,
            duplicate=False,
            uploadedBytes=upload.uploaded_bytes,
            missingChunks=_missing_chunks(session, upload),
        )


def complete_attachment_upload(
    *,
    upload_id: str,
    protocol_version: int,
    user: UserEntity,
    device_id: str,
    storage: FilesystemAttachmentStorage | None = None,
) -> AttachmentUploadStatus:
    _protocol(protocol_version)
    storage = storage or FilesystemAttachmentStorage()
    with SessionLocal() as session, session.begin():
        upload = session.scalar(
            select(AttachmentUploadSessionRow)
            .where(
                AttachmentUploadSessionRow.owner_id == user.id,
                AttachmentUploadSessionRow.device_id == device_id,
                AttachmentUploadSessionRow.public_id == upload_id,
            )
            .with_for_update()
        )
        if upload is None:
            raise api_error(status.HTTP_404_NOT_FOUND, "SYNC_ENTITY_NOT_FOUND", "Attachment upload not found")
        if upload.status == "completed":
            return _upload_status(session, upload)
        if as_aware_utc(upload.expires_at) <= now_utc():
            raise api_error(status.HTTP_410_GONE, "SYNC_UPLOAD_EXPIRED", "Attachment upload has expired")
        chunks = list(
            session.scalars(
                select(AttachmentUploadChunkRow)
                .where(AttachmentUploadChunkRow.session_id == upload.id)
                .order_by(AttachmentUploadChunkRow.ordinal)
            )
        )
        missing = _missing_chunks(session, upload)
        if missing or upload.uploaded_bytes != upload.byte_size:
            raise api_error(
                status.HTTP_409_CONFLICT,
                "SYNC_UPLOAD_INCOMPLETE",
                "Attachment upload is missing chunks",
                {"missingChunks": missing},
            )
        try:
            storage_key = storage.assemble(
                [chunk.storage_key for chunk in chunks],
                content_hash=upload.content_hash,
                byte_size=upload.byte_size,
            )
        except AttachmentStorageError as error:
            raise api_error(status.HTTP_422_UNPROCESSABLE_ENTITY, "SYNC_ATTACHMENT_HASH_MISMATCH", str(error)) from error
        now = now_utc()
        blob = session.scalar(select(AttachmentBlobRow).where(AttachmentBlobRow.sha256 == upload.content_hash).with_for_update())
        if blob is None or blob.byte_size != upload.byte_size:
            raise api_error(status.HTTP_422_UNPROCESSABLE_ENTITY, "SYNC_ATTACHMENT_HASH_MISMATCH", "Attachment digest metadata differs")
        blob.storage_key = storage_key
        blob.status = "available"
        blob.verified_at = now
        blob.updated_at = now
        session.flush()
        reference = session.get(AttachmentReferenceRow, upload.reference_id)
        if reference is None or reference.tombstone:
            raise api_error(status.HTTP_409_CONFLICT, "SYNC_CONFLICT", "Attachment reference is unavailable")
        reference.blob_id = blob.id
        reference.availability = "available"
        reference.updated_at = now
        upload.blob_id = blob.id
        upload.status = "completed"
        upload.completed_at = now
        upload.updated_at = now
        session.flush()
        return _upload_status(session, upload)


def download_attachment(
    *,
    attachment_id: str,
    user: UserEntity,
    storage: FilesystemAttachmentStorage | None = None,
) -> AttachmentDownload:
    storage = storage or FilesystemAttachmentStorage()
    with SessionLocal() as session:
        grant = require_attachment_download(session, reference_public_id=attachment_id, current_user=user)
        try:
            content = storage.read_verified(
                grant.blob.storage_key,
                content_hash=grant.blob.sha256,
                byte_size=grant.blob.byte_size,
            )
        except AttachmentStorageError as error:
            grant.blob.status = "quarantined"
            grant.blob.updated_at = now_utc()
            session.commit()
            raise api_error(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "SYNC_ATTACHMENT_HASH_MISMATCH",
                "Attachment integrity check failed",
            ) from error
        return AttachmentDownload(
            content=content,
            file_name=grant.reference.file_name,
            content_type=grant.reference.content_type,
            content_hash=grant.blob.sha256,
        )
