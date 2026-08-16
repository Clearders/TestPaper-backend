"""add content-addressed attachment blobs, references, and upload sessions

Revision ID: 20260813_0021
Revises: 20260813_0020
Create Date: 2026-08-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260813_0021"
down_revision: str | None = "20260813_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_sync_entities_id_owner_scope",
        "sync_entities",
        ["id", "ownerId", "scope"],
    )
    op.create_table(
        "attachment_blobs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("byteSize", sa.BigInteger(), nullable=False),
        sa.Column("contentType", sa.String(255), nullable=True),
        sa.Column("storageKey", sa.String(512), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("referenceCount", sa.BigInteger(), nullable=False),
        sa.Column("verifiedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("gcEligibleAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updatedAt", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_attachment_blobs_sha256"),
        sa.CheckConstraint('"byteSize" >= 0', name="ck_attachment_blobs_byte_size"),
        sa.CheckConstraint('"referenceCount" >= 0', name="ck_attachment_blobs_reference_count"),
        sa.CheckConstraint("status IN ('pending', 'available', 'quarantined')", name="ck_attachment_blobs_status"),
        sa.CheckConstraint("status <> 'available' OR \"verifiedAt\" IS NOT NULL", name="ck_attachment_blobs_available_verified"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sha256", name="uq_attachment_blobs_sha256"),
        sa.UniqueConstraint("storageKey", name="uq_attachment_blobs_storage_key"),
    )
    op.create_index(
        "ix_attachment_blobs_gc",
        "attachment_blobs",
        ["status", "referenceCount", "gcEligibleAt"],
        unique=False,
    )

    op.create_table(
        "attachment_references",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("publicId", sa.String(36), nullable=False),
        sa.Column("ownerId", sa.Integer(), nullable=False),
        sa.Column("scope", sa.String(64), nullable=False),
        sa.Column("attachmentEntityId", sa.BigInteger(), nullable=False),
        sa.Column("targetEntityId", sa.BigInteger(), nullable=False),
        sa.Column("blobId", sa.BigInteger(), nullable=True),
        sa.Column("contentHash", sa.String(64), nullable=False),
        sa.Column("byteSize", sa.BigInteger(), nullable=False),
        sa.Column("fileName", sa.String(255), nullable=False),
        sa.Column("contentType", sa.String(255), nullable=False),
        sa.Column("availability", sa.String(16), nullable=False),
        sa.Column("tombstone", sa.Boolean(), nullable=False),
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updatedAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deletedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retentionUntil", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint('length("publicId") = 36', name="ck_attachment_references_public_id"),
        sa.CheckConstraint('"contentHash" ~ \'^[0-9a-f]{64}$\'', name="ck_attachment_references_content_hash"),
        sa.CheckConstraint('"byteSize" >= 0', name="ck_attachment_references_byte_size"),
        sa.CheckConstraint("availability IN ('pending', 'available')", name="ck_attachment_references_availability"),
        sa.CheckConstraint(
            '(availability = \'available\' AND "blobId" IS NOT NULL) OR '
            '(availability = \'pending\' AND "blobId" IS NULL)',
            name="ck_attachment_references_blob_availability",
        ),
        sa.CheckConstraint(
            '(tombstone AND "deletedAt" IS NOT NULL AND "retentionUntil" IS NOT NULL '
            'AND "retentionUntil" >= "deletedAt") OR '
            '(NOT tombstone AND "deletedAt" IS NULL AND "retentionUntil" IS NULL)',
            name="ck_attachment_references_tombstone_retention",
        ),
        sa.ForeignKeyConstraint(["blobId"], ["attachment_blobs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["ownerId"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["targetEntityId", "ownerId", "scope"],
            ["sync_entities.id", "sync_entities.ownerId", "sync_entities.scope"],
            ondelete="CASCADE",
            name="fk_attachment_references_target_acl",
        ),
        sa.ForeignKeyConstraint(
            ["attachmentEntityId", "ownerId", "scope"],
            ["sync_entities.id", "sync_entities.ownerId", "sync_entities.scope"],
            ondelete="CASCADE",
            name="fk_attachment_references_sync_identity",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "ownerId", name="uq_attachment_references_id_owner"),
        sa.UniqueConstraint("ownerId", "publicId", name="uq_attachment_references_owner_public_id"),
    )
    op.create_index(
        "ix_attachment_references_owner_target",
        "attachment_references",
        ["ownerId", "targetEntityId", "tombstone"],
        unique=False,
    )

    # The metadata identity is an attachment Sync projection, while the separate target entity
    # owns its ACL. Both composite foreign keys already enforce the same owner and scope.
    op.execute(
        sa.text(
            """
            CREATE FUNCTION validate_attachment_reference_identity() RETURNS trigger AS $$
            DECLARE
                attachment_type varchar(32);
                attachment_public_id varchar(36);
            BEGIN
                SELECT "entityType", "publicId"
                INTO attachment_type, attachment_public_id
                FROM sync_entities
                WHERE id = NEW."attachmentEntityId"
                  AND "ownerId" = NEW."ownerId"
                  AND scope = NEW.scope
                FOR SHARE;
                IF attachment_type IS DISTINCT FROM 'attachment'
                   OR attachment_public_id IS DISTINCT FROM NEW."publicId" THEN
                    RAISE EXCEPTION 'attachment reference Sync identity mismatch'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;

            CREATE TRIGGER trg_attachment_reference_sync_identity
            BEFORE INSERT OR UPDATE OF "attachmentEntityId", "publicId", "ownerId", scope
            ON attachment_references
            FOR EACH ROW EXECUTE FUNCTION validate_attachment_reference_identity();
            """
        )
    )
    op.create_index(
        "ix_attachment_references_blob",
        "attachment_references",
        ["blobId", "tombstone"],
        unique=False,
    )

    op.create_table(
        "attachment_upload_sessions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ownerId", sa.Integer(), nullable=False),
        sa.Column("deviceId", sa.String(128), nullable=False),
        sa.Column("referenceId", sa.BigInteger(), nullable=False),
        sa.Column("idempotencyKey", sa.String(128), nullable=False),
        sa.Column("requestHash", sa.String(64), nullable=False),
        sa.Column("contentHash", sa.String(64), nullable=False),
        sa.Column("byteSize", sa.BigInteger(), nullable=False),
        sa.Column("chunkSize", sa.Integer(), nullable=False),
        sa.Column("totalChunks", sa.Integer(), nullable=False),
        sa.Column("uploadedBytes", sa.BigInteger(), nullable=False),
        sa.Column("storagePrefix", sa.String(512), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("blobId", sa.BigInteger(), nullable=True),
        sa.Column("expiresAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updatedAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completedAt", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint('"requestHash" ~ \'^[0-9a-f]{64}$\'', name="ck_attachment_upload_sessions_request_hash"),
        sa.CheckConstraint('"contentHash" ~ \'^[0-9a-f]{64}$\'', name="ck_attachment_upload_sessions_content_hash"),
        sa.CheckConstraint('"byteSize" >= 0', name="ck_attachment_upload_sessions_byte_size"),
        sa.CheckConstraint('"chunkSize" > 0', name="ck_attachment_upload_sessions_chunk_size"),
        sa.CheckConstraint('"totalChunks" > 0', name="ck_attachment_upload_sessions_total_chunks"),
        sa.CheckConstraint(
            '"uploadedBytes" >= 0 AND "uploadedBytes" <= "byteSize"',
            name="ck_attachment_upload_sessions_uploaded_bytes",
        ),
        sa.CheckConstraint(
            "status IN ('initiated', 'uploading', 'completed', 'expired', 'aborted')",
            name="ck_attachment_upload_sessions_status",
        ),
        sa.CheckConstraint(
            '(status = \'completed\' AND "blobId" IS NOT NULL AND "completedAt" IS NOT NULL) OR '
            '(status <> \'completed\' AND "completedAt" IS NULL)',
            name="ck_attachment_upload_sessions_completion",
        ),
        sa.CheckConstraint('"expiresAt" > "createdAt"', name="ck_attachment_upload_sessions_expiry"),
        sa.ForeignKeyConstraint(["blobId"], ["attachment_blobs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["ownerId"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["referenceId", "ownerId"],
            ["attachment_references.id", "attachment_references.ownerId"],
            ondelete="CASCADE",
            name="fk_attachment_upload_sessions_reference_owner",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "ownerId", name="uq_attachment_upload_sessions_id_owner"),
        sa.UniqueConstraint(
            "ownerId",
            "deviceId",
            "idempotencyKey",
            name="uq_attachment_upload_sessions_owner_device_key",
        ),
        sa.UniqueConstraint("storagePrefix", name="uq_attachment_upload_sessions_storage_prefix"),
    )
    op.create_index(
        "ix_attachment_upload_sessions_expiry",
        "attachment_upload_sessions",
        ["status", "expiresAt"],
        unique=False,
    )
    op.create_index(
        "ix_attachment_upload_sessions_owner_reference",
        "attachment_upload_sessions",
        ["ownerId", "referenceId"],
        unique=False,
    )

    op.create_table(
        "attachment_upload_chunks",
        sa.Column("sessionId", sa.BigInteger(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("byteOffset", sa.BigInteger(), nullable=False),
        sa.Column("byteSize", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("storageKey", sa.String(512), nullable=False),
        sa.Column("verifiedAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("ordinal >= 0", name="ck_attachment_upload_chunks_ordinal"),
        sa.CheckConstraint('"byteOffset" >= 0', name="ck_attachment_upload_chunks_byte_offset"),
        sa.CheckConstraint('"byteSize" > 0', name="ck_attachment_upload_chunks_byte_size"),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_attachment_upload_chunks_sha256"),
        sa.ForeignKeyConstraint(["sessionId"], ["attachment_upload_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("sessionId", "ordinal"),
        sa.UniqueConstraint("storageKey", name="uq_attachment_upload_chunks_storage_key"),
    )

    # A reference can only bind verified bytes matching its immutable hash and size metadata.
    op.execute(
        sa.text(
            """
            CREATE FUNCTION validate_attachment_reference_blob() RETURNS trigger AS $$
            DECLARE
                candidate attachment_blobs%ROWTYPE;
            BEGIN
                IF NEW."blobId" IS NULL THEN
                    RETURN NEW;
                END IF;
                SELECT * INTO candidate FROM attachment_blobs WHERE id = NEW."blobId" FOR SHARE;
                IF candidate.id IS NULL OR candidate.status <> 'available'
                   OR candidate.sha256 <> NEW."contentHash"
                   OR candidate."byteSize" <> NEW."byteSize" THEN
                    RAISE EXCEPTION 'attachment reference blob metadata mismatch'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;

            CREATE TRIGGER trg_attachment_reference_blob_integrity
            BEFORE INSERT OR UPDATE OF "blobId", "contentHash", "byteSize", availability
            ON attachment_references
            FOR EACH ROW EXECUTE FUNCTION validate_attachment_reference_blob();
            """
        )
    )

    # referenceCount covers live references only. Tombstone retention accumulates as a lower
    # bound for garbage collection, and a blob row can never underflow.
    op.execute(
        sa.text(
            """
            CREATE FUNCTION maintain_attachment_blob_reference_count() RETURNS trigger AS $$
            DECLARE
                old_live boolean := false;
                new_live boolean := false;
                retention_bound timestamptz;
            BEGIN
                IF TG_OP <> 'INSERT' THEN
                    old_live := OLD."blobId" IS NOT NULL AND NOT OLD.tombstone;
                END IF;
                IF TG_OP <> 'DELETE' THEN
                    new_live := NEW."blobId" IS NOT NULL AND NOT NEW.tombstone;
                END IF;
                IF old_live AND (NOT new_live OR NEW."blobId" <> OLD."blobId") THEN
                    IF TG_OP = 'DELETE' THEN
                        retention_bound := COALESCE(OLD."retentionUntil", clock_timestamp() + interval '30 days');
                    ELSE
                        retention_bound := COALESCE(NEW."retentionUntil", OLD."retentionUntil", clock_timestamp() + interval '30 days');
                    END IF;
                    UPDATE attachment_blobs
                    SET "referenceCount" = "referenceCount" - 1,
                        "gcEligibleAt" = GREATEST(COALESCE("gcEligibleAt", retention_bound), retention_bound),
                        "updatedAt" = clock_timestamp()
                    WHERE id = OLD."blobId" AND "referenceCount" > 0;
                    IF NOT FOUND THEN
                        RAISE EXCEPTION 'attachment blob reference count underflow'
                            USING ERRCODE = '23514';
                    END IF;
                END IF;
                IF new_live AND (NOT old_live OR NEW."blobId" <> OLD."blobId") THEN
                    UPDATE attachment_blobs
                    SET "referenceCount" = "referenceCount" + 1,
                        "updatedAt" = clock_timestamp()
                    WHERE id = NEW."blobId";
                    IF NOT FOUND THEN
                        RAISE EXCEPTION 'attachment blob does not exist'
                            USING ERRCODE = '23503';
                    END IF;
                END IF;
                IF TG_OP = 'DELETE' THEN
                    RETURN OLD;
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;

            CREATE TRIGGER trg_attachment_blob_reference_count
            AFTER INSERT OR UPDATE OF "blobId", tombstone OR DELETE
            ON attachment_references
            FOR EACH ROW EXECUTE FUNCTION maintain_attachment_blob_reference_count();
            """
        )
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_attachment_blob_reference_count ON attachment_references")
    op.execute("DROP FUNCTION IF EXISTS maintain_attachment_blob_reference_count()")
    op.execute("DROP TRIGGER IF EXISTS trg_attachment_reference_blob_integrity ON attachment_references")
    op.execute("DROP FUNCTION IF EXISTS validate_attachment_reference_blob()")
    op.execute("DROP TRIGGER IF EXISTS trg_attachment_reference_sync_identity ON attachment_references")
    op.execute("DROP FUNCTION IF EXISTS validate_attachment_reference_identity()")
    op.drop_table("attachment_upload_chunks")
    op.drop_index("ix_attachment_upload_sessions_owner_reference", table_name="attachment_upload_sessions")
    op.drop_index("ix_attachment_upload_sessions_expiry", table_name="attachment_upload_sessions")
    op.drop_table("attachment_upload_sessions")
    op.drop_index("ix_attachment_references_blob", table_name="attachment_references")
    op.drop_index("ix_attachment_references_owner_target", table_name="attachment_references")
    op.drop_table("attachment_references")
    op.drop_index("ix_attachment_blobs_gc", table_name="attachment_blobs")
    op.drop_table("attachment_blobs")
    op.drop_constraint("uq_sync_entities_id_owner_scope", "sync_entities", type_="unique")
