from __future__ import annotations

from dataclasses import dataclass

from fastapi import status
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from testpaper_backend.core.errors import api_error
from testpaper_backend.db import AttachmentBlobRow, AttachmentReferenceRow, SyncEntityRow
from testpaper_backend.schemas import UserEntity


@dataclass(frozen=True, slots=True)
class AttachmentDownloadGrant:
    reference: AttachmentReferenceRow
    blob: AttachmentBlobRow


def authorized_attachment_statement(reference_public_id: str, current_user: UserEntity) -> Select:
    """Resolve bytes only through an active reference and its inherited target ACL.

    Deliberately accepting a reference public ID instead of a blob ID prevents content-addressed
    storage identifiers and filesystem/object-store keys from becoming authorization capabilities.
    M4's personal scope is owner-only; future shared scopes must add an explicit target ACL branch.
    """

    return (
        select(AttachmentReferenceRow, AttachmentBlobRow)
        .join(
            SyncEntityRow,
            (SyncEntityRow.id == AttachmentReferenceRow.target_entity_id)
            & (SyncEntityRow.owner_id == AttachmentReferenceRow.owner_id)
            & (SyncEntityRow.scope == AttachmentReferenceRow.scope),
        )
        .join(AttachmentBlobRow, AttachmentBlobRow.id == AttachmentReferenceRow.blob_id)
        .where(
            AttachmentReferenceRow.public_id == reference_public_id,
            AttachmentReferenceRow.owner_id == current_user.id,
            AttachmentReferenceRow.scope == "personal",
            AttachmentReferenceRow.availability == "available",
            AttachmentReferenceRow.tombstone.is_(False),
            SyncEntityRow.tombstone.is_(False),
            AttachmentBlobRow.status == "available",
        )
    )


def require_attachment_download(
    session: Session,
    *,
    reference_public_id: str,
    current_user: UserEntity,
) -> AttachmentDownloadGrant:
    row = session.execute(authorized_attachment_statement(reference_public_id, current_user)).one_or_none()
    if row is None:
        # The same response covers absent, tombstoned, and forbidden references so identifiers
        # cannot be enumerated across accounts.
        raise api_error(status.HTTP_404_NOT_FOUND, "SYNC_ENTITY_NOT_FOUND", "Attachment not found")
    reference, blob = row
    return AttachmentDownloadGrant(reference=reference, blob=blob)
