from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.dialects import postgresql

from testpaper_backend.db import AttachmentBlobRow, AttachmentReferenceRow, Base
from testpaper_backend.services.attachment_access import authorized_attachment_statement, require_attachment_download

ATTACHMENT_TABLES = {
    "attachment_blobs",
    "attachment_gc_audit",
    "attachment_references",
    "attachment_upload_sessions",
    "attachment_upload_chunks",
}


def named_constraints(table_name: str, constraint_type: type) -> set[str | None]:
    table = Base.metadata.tables[table_name]
    return {constraint.name for constraint in table.constraints if isinstance(constraint, constraint_type)}


def test_content_addressed_blob_and_reference_lifecycle_are_database_backed() -> None:
    assert set(Base.metadata.tables) >= ATTACHMENT_TABLES
    assert "uq_attachment_blobs_sha256" in named_constraints("attachment_blobs", UniqueConstraint)
    assert "ck_attachment_blobs_reference_count" in named_constraints("attachment_blobs", CheckConstraint)
    assert "ck_attachment_blobs_available_verified" in named_constraints("attachment_blobs", CheckConstraint)
    assert {
        "ck_attachment_references_blob_availability",
        "ck_attachment_references_tombstone_retention",
    } <= named_constraints("attachment_references", CheckConstraint)

    gc_index = Base.metadata.tables["attachment_blobs"].indexes
    assert {index.name: tuple(column.name for column in index.columns) for index in gc_index}["ix_attachment_blobs_gc"] == (
        "status",
        "referenceCount",
        "gcEligibleAt",
    )


def test_reference_owner_and_scope_are_bound_to_the_target_acl() -> None:
    constraints = [
        constraint
        for constraint in Base.metadata.tables["attachment_references"].constraints
        if isinstance(constraint, ForeignKeyConstraint)
    ]
    acl = next(constraint for constraint in constraints if constraint.name == "fk_attachment_references_target_acl")
    assert tuple(element.parent.name for element in acl.elements) == ("targetEntityId", "ownerId", "scope")
    assert tuple(element.column.name for element in acl.elements) == ("id", "ownerId", "scope")
    identity = next(constraint for constraint in constraints if constraint.name == "fk_attachment_references_sync_identity")
    assert tuple(element.parent.name for element in identity.elements) == ("attachmentEntityId", "ownerId", "scope")
    assert "uq_sync_entities_id_owner_scope" in named_constraints("sync_entities", UniqueConstraint)


def test_upload_state_is_replayable_without_embedding_bytes() -> None:
    session_table = Base.metadata.tables["attachment_upload_sessions"]
    assert "uq_attachment_upload_sessions_owner_device_key" in named_constraints("attachment_upload_sessions", UniqueConstraint)
    assert "ck_attachment_upload_sessions_completion" in named_constraints("attachment_upload_sessions", CheckConstraint)
    assert "ck_attachment_upload_sessions_request_hash" in named_constraints("attachment_upload_sessions", CheckConstraint)
    assert tuple(column.name for column in Base.metadata.tables["attachment_upload_chunks"].primary_key.columns) == (
        "sessionId",
        "ordinal",
    )
    assert "payload" not in session_table.columns and "bytes" not in session_table.columns


def test_garbage_collection_audit_is_bounded_and_append_only() -> None:
    table = Base.metadata.tables["attachment_gc_audit"]
    assert "ck_attachment_gc_audit_action" in named_constraints("attachment_gc_audit", CheckConstraint)
    assert "ix_attachment_gc_audit_created" in {index.name for index in table.indexes}
    assert "payload" not in table.columns and "bytes" not in table.columns


def test_download_query_never_treats_blob_identity_as_acl() -> None:
    statement = authorized_attachment_statement(
        "11111111-1111-4111-8111-111111111111",
        SimpleNamespace(id=7),
    )
    sql = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "attachment_references" in sql and "sync_entities" in sql and "attachment_blobs" in sql
    assert 'attachment_references."ownerId" = 7' in sql
    assert "attachment_references.scope = 'personal'" in sql
    assert 'sync_entities."ownerId" = attachment_references."ownerId"' in sql
    assert "attachment_blobs.storageKey" not in sql


class _MissingResult:
    @staticmethod
    def one_or_none():
        return None


class _MissingSession:
    @staticmethod
    def execute(_statement):
        return _MissingResult()


def test_missing_and_unauthorized_downloads_share_non_enumerable_error() -> None:
    with pytest.raises(HTTPException) as error:
        require_attachment_download(
            _MissingSession(),
            reference_public_id="11111111-1111-4111-8111-111111111111",
            current_user=SimpleNamespace(id=7),
        )
    assert error.value.status_code == 404
    assert error.value.detail == {"code": "SYNC_ENTITY_NOT_FOUND", "message": "Attachment not found"}


class _GrantedResult:
    def __init__(self, value):
        self.value = value

    def one_or_none(self):
        return self.value


class _GrantedSession:
    def __init__(self, value):
        self.value = value

    def execute(self, _statement):
        return _GrantedResult(self.value)


def test_authorized_download_returns_separate_metadata_and_blob_identity() -> None:
    reference = AttachmentReferenceRow()
    blob = AttachmentBlobRow()
    grant = require_attachment_download(
        _GrantedSession((reference, blob)),
        reference_public_id="11111111-1111-4111-8111-111111111111",
        current_user=SimpleNamespace(id=7),
    )
    assert grant.reference is reference
    assert grant.blob is blob
