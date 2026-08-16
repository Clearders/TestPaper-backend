from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, delete, func, or_, select, update

from testpaper_backend.db import (
    AttachmentBlobRow,
    AttachmentGcAuditRow,
    AttachmentReferenceRow,
    AttachmentUploadChunkRow,
    AttachmentUploadSessionRow,
    SessionLocal,
    SyncEntityRow,
)
from testpaper_backend.services.attachment_storage import AttachmentStorageError, FilesystemAttachmentStorage
from testpaper_backend.time_utils import now_utc

ATTACHMENT_RETENTION_DAYS = 30
EXPIRED_UPLOAD_GRACE_DAYS = 7


@dataclass(frozen=True, slots=True)
class AttachmentMaintenanceResult:
    expired_uploads_marked: int = 0
    expired_uploads_deleted: int = 0
    blobs_deleted: int = 0
    files_deleted: int = 0
    file_delete_failures: int = 0


def apply_attachment_reference_lifecycle(
    session,
    *,
    entity: SyncEntityRow,
    mutation_kind: str,
    occurred_at: datetime,
) -> None:
    if entity.entity_type != "attachment" or mutation_kind not in {"delete", "restore"}:
        return
    reference = session.scalar(
        select(AttachmentReferenceRow)
        .where(
            AttachmentReferenceRow.owner_id == entity.owner_id,
            AttachmentReferenceRow.attachment_entity_id == entity.id,
        )
        .with_for_update()
    )
    if reference is None:
        return
    if mutation_kind == "delete" and not reference.tombstone:
        reference.tombstone = True
        reference.deleted_at = occurred_at
        reference.retention_until = occurred_at + timedelta(days=ATTACHMENT_RETENTION_DAYS)
        reference.updated_at = occurred_at
    elif mutation_kind == "restore" and reference.tombstone:
        reference.tombstone = False
        reference.deleted_at = None
        reference.retention_until = None
        reference.updated_at = occurred_at


def _audit(
    *,
    action: str,
    target_kind: str,
    target_id: str,
    content_hash: str | None,
    details: dict[str, object],
    created_at: datetime,
):
    return AttachmentGcAuditRow(
        action=action,
        target_kind=target_kind,
        target_id=target_id,
        content_hash=content_hash,
        details=details,
        created_at=created_at,
    )


def run_attachment_maintenance(
    *,
    storage: FilesystemAttachmentStorage | None = None,
    current_time: datetime | None = None,
    batch_size: int = 100,
) -> AttachmentMaintenanceResult:
    storage = storage or FilesystemAttachmentStorage()
    current_time = current_time or now_utc()
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    grace_cutoff = current_time - timedelta(days=EXPIRED_UPLOAD_GRACE_DAYS)
    expired_marked = expired_deleted = blobs_deleted = files_deleted = file_failures = 0
    file_keys: list[tuple[str, str, str]] = []

    with SessionLocal() as session, session.begin():
        uploads = list(
            session.scalars(
                select(AttachmentUploadSessionRow)
                .where(
                    AttachmentUploadSessionRow.status.in_(["initiated", "uploading", "expired"]),
                    AttachmentUploadSessionRow.expires_at <= current_time,
                )
                .order_by(AttachmentUploadSessionRow.id)
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
        )
        for upload in uploads:
            if upload.status != "expired":
                upload.status = "expired"
                upload.updated_at = current_time
                expired_marked += 1
            if upload.expires_at > grace_cutoff:
                continue
            chunk_keys = list(
                session.scalars(select(AttachmentUploadChunkRow.storage_key).where(AttachmentUploadChunkRow.session_id == upload.id))
            )
            file_keys.extend((key, "upload", upload.public_id) for key in chunk_keys)
            session.add(
                _audit(
                    action="expired_upload_deleted",
                    target_kind="upload",
                    target_id=upload.public_id,
                    content_hash=upload.content_hash,
                    details={"chunkCount": len(chunk_keys), "graceDays": EXPIRED_UPLOAD_GRACE_DAYS},
                    created_at=current_time,
                )
            )
            session.delete(upload)
            expired_deleted += 1

    with SessionLocal() as session, session.begin():
        blobs = list(
            session.scalars(
                select(AttachmentBlobRow)
                .where(
                    AttachmentBlobRow.reference_count == 0,
                    or_(
                        and_(
                            AttachmentBlobRow.gc_eligible_at.is_not(None),
                            AttachmentBlobRow.gc_eligible_at <= current_time,
                        ),
                        and_(
                            AttachmentBlobRow.status.in_(["pending", "quarantined"]),
                            AttachmentBlobRow.created_at <= grace_cutoff,
                        ),
                    ),
                )
                .order_by(AttachmentBlobRow.id)
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
        )
        for blob in blobs:
            live_references = session.scalar(
                select(func.count())
                .select_from(AttachmentReferenceRow)
                .where(
                    AttachmentReferenceRow.blob_id == blob.id,
                    AttachmentReferenceRow.tombstone.is_(False),
                )
            )
            retained_references = session.scalar(
                select(func.count())
                .select_from(AttachmentReferenceRow)
                .where(
                    AttachmentReferenceRow.blob_id == blob.id,
                    AttachmentReferenceRow.retention_until > current_time,
                )
            )
            active_uploads = session.scalar(
                select(func.count())
                .select_from(AttachmentUploadSessionRow)
                .where(
                    or_(
                        AttachmentUploadSessionRow.blob_id == blob.id,
                        AttachmentUploadSessionRow.content_hash == blob.sha256,
                    ),
                    AttachmentUploadSessionRow.expires_at > current_time,
                )
            )
            if live_references or retained_references or active_uploads:
                continue
            session.execute(delete(AttachmentUploadSessionRow).where(AttachmentUploadSessionRow.blob_id == blob.id))
            detached = session.execute(
                update(AttachmentReferenceRow)
                .where(AttachmentReferenceRow.blob_id == blob.id, AttachmentReferenceRow.tombstone.is_(True))
                .values(blob_id=None, availability="pending", updated_at=current_time)
            ).rowcount
            session.add(
                _audit(
                    action="blob_metadata_deleted",
                    target_kind="blob",
                    target_id=str(blob.id),
                    content_hash=blob.sha256,
                    details={"detachedTombstones": detached, "storageKey": blob.storage_key},
                    created_at=current_time,
                )
            )
            file_keys.append((blob.storage_key, "blob", str(blob.id)))
            session.delete(blob)
            blobs_deleted += 1

    for key, target_kind, target_id in file_keys:
        action = "blob_file_deleted"
        deleted = False
        try:
            deleted = storage.delete(key)
            files_deleted += int(deleted)
        except AttachmentStorageError:
            action = "blob_file_delete_failed"
            file_failures += 1
        if target_kind == "blob":
            with SessionLocal() as session, session.begin():
                session.add(
                    _audit(
                        action=action,
                        target_kind=target_kind,
                        target_id=target_id,
                        content_hash=None,
                        details={"storageKey": key, "fileExisted": deleted},
                        created_at=current_time,
                    )
                )

    return AttachmentMaintenanceResult(
        expired_uploads_marked=expired_marked,
        expired_uploads_deleted=expired_deleted,
        blobs_deleted=blobs_deleted,
        files_deleted=files_deleted,
        file_delete_failures=file_failures,
    )
