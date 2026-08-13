"""add sync projections, versions, cursors, and idempotency records

Revision ID: 20260813_0019
Revises: 20260809_0018
Create Date: 2026-08-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260813_0019"
down_revision: str | None = "20260809_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ENTITY_TYPES = "'question', 'paper', 'draft', 'attachment', 'comment', 'favorite', 'setting'"


def upgrade() -> None:
    op.create_table(
        "sync_entities",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ownerId", sa.Integer(), nullable=False),
        sa.Column("entityType", sa.String(32), nullable=False),
        sa.Column("publicId", sa.String(36), nullable=False),
        sa.Column("scope", sa.String(64), nullable=False),
        sa.Column("schemaVersion", sa.Integer(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("contentHash", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column("tombstone", sa.Boolean(), nullable=False),
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updatedAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deletedAt", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(f'"entityType" IN ({ENTITY_TYPES})', name="ck_sync_entities_entity_type"),
        sa.CheckConstraint('"schemaVersion" >= 1', name="ck_sync_entities_schema_version"),
        sa.CheckConstraint('"version" >= 1', name="ck_sync_entities_version"),
        sa.CheckConstraint('length("contentHash") = 64', name="ck_sync_entities_content_hash"),
        sa.CheckConstraint(
            '("tombstone" AND "deletedAt" IS NOT NULL) OR (NOT "tombstone" AND "deletedAt" IS NULL)',
            name="ck_sync_entities_tombstone_deleted_at",
        ),
        sa.ForeignKeyConstraint(["ownerId"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "ownerId", name="uq_sync_entities_id_owner"),
        sa.UniqueConstraint("ownerId", "entityType", "publicId", name="uq_sync_entities_owner_type_public_id"),
    )
    op.create_index(
        "ix_sync_entities_owner_scope_updated",
        "sync_entities",
        ["ownerId", "scope", "updatedAt", "id"],
        unique=False,
    )
    op.create_index(
        "ix_sync_entities_owner_type_tombstone",
        "sync_entities",
        ["ownerId", "entityType", "tombstone"],
        unique=False,
    )

    op.create_table(
        "sync_streams",
        sa.Column("ownerId", sa.Integer(), nullable=False),
        sa.Column("scope", sa.String(64), nullable=False),
        sa.Column("epoch", sa.String(36), nullable=False),
        sa.Column("retainedFromSequence", sa.BigInteger(), nullable=False),
        sa.Column("snapshotVersion", sa.BigInteger(), nullable=False),
        sa.Column("compactedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updatedAt", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('"retainedFromSequence" >= 0', name="ck_sync_streams_retained_sequence"),
        sa.CheckConstraint('"snapshotVersion" >= 0', name="ck_sync_streams_snapshot_version"),
        sa.ForeignKeyConstraint(["ownerId"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("ownerId", "scope"),
    )

    op.create_table(
        "sync_entity_versions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("entityId", sa.BigInteger(), nullable=False),
        sa.Column("ownerId", sa.Integer(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("schemaVersion", sa.Integer(), nullable=False),
        sa.Column("contentHash", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column("tombstone", sa.Boolean(), nullable=False),
        sa.Column("mutationKind", sa.String(16), nullable=False),
        sa.Column("operationId", sa.String(36), nullable=False),
        sa.Column("baseVersion", sa.BigInteger(), nullable=True),
        sa.Column("baseHash", sa.String(64), nullable=True),
        sa.Column("deviceId", sa.String(128), nullable=False),
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('"version" >= 1', name="ck_sync_entity_versions_version"),
        sa.CheckConstraint('"schemaVersion" >= 1', name="ck_sync_entity_versions_schema_version"),
        sa.CheckConstraint('length("contentHash") = 64', name="ck_sync_entity_versions_content_hash"),
        sa.CheckConstraint(
            '"mutationKind" IN (\'create\', \'update\', \'delete\', \'restore\')',
            name="ck_sync_entity_versions_mutation_kind",
        ),
        sa.ForeignKeyConstraint(
            ["entityId", "ownerId"],
            ["sync_entities.id", "sync_entities.ownerId"],
            ondelete="CASCADE",
            name="fk_sync_entity_versions_entity_owner",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("entityId", "version", name="uq_sync_entity_versions_entity_version"),
        sa.UniqueConstraint("ownerId", "operationId", name="uq_sync_entity_versions_owner_operation"),
    )
    op.create_index(
        "ix_sync_entity_versions_entity_created",
        "sync_entity_versions",
        ["entityId", "createdAt"],
        unique=False,
    )

    op.create_table(
        "sync_change_log",
        sa.Column("sequence", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("entityVersionId", sa.BigInteger(), nullable=False),
        sa.Column("ownerId", sa.Integer(), nullable=False),
        sa.Column("scope", sa.String(64), nullable=False),
        sa.Column("entityType", sa.String(32), nullable=False),
        sa.Column("publicId", sa.String(36), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("contentHash", sa.String(64), nullable=False),
        sa.Column("mutationKind", sa.String(16), nullable=False),
        sa.Column("tombstone", sa.Boolean(), nullable=False),
        sa.Column("operationId", sa.String(36), nullable=False),
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('"version" >= 1', name="ck_sync_change_log_version"),
        sa.CheckConstraint('length("contentHash") = 64', name="ck_sync_change_log_content_hash"),
        sa.ForeignKeyConstraint(["entityVersionId"], ["sync_entity_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ownerId"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("sequence"),
        sa.UniqueConstraint("entityVersionId", name="uq_sync_change_log_entity_version"),
    )
    op.create_index(
        "ix_sync_change_log_pull",
        "sync_change_log",
        ["ownerId", "scope", "sequence"],
        unique=False,
    )
    op.create_index(
        "ix_sync_change_log_compaction",
        "sync_change_log",
        ["ownerId", "createdAt", "sequence"],
        unique=False,
    )

    op.create_table(
        "sync_device_cursors",
        sa.Column("ownerId", sa.Integer(), nullable=False),
        sa.Column("deviceId", sa.String(128), nullable=False),
        sa.Column("scope", sa.String(64), nullable=False),
        sa.Column("streamEpoch", sa.String(36), nullable=False),
        sa.Column("cursorSequence", sa.BigInteger(), nullable=False),
        sa.Column("protocolVersion", sa.Integer(), nullable=False),
        sa.Column("lastAckAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lastSeenAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expiresAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revokedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updatedAt", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('"cursorSequence" >= 0', name="ck_sync_device_cursors_sequence"),
        sa.CheckConstraint('"protocolVersion" >= 1', name="ck_sync_device_cursors_protocol_version"),
        sa.ForeignKeyConstraint(
            ["ownerId", "scope"],
            ["sync_streams.ownerId", "sync_streams.scope"],
            ondelete="CASCADE",
            name="fk_sync_device_cursors_stream",
        ),
        sa.PrimaryKeyConstraint("ownerId", "deviceId", "scope"),
    )
    op.create_index(
        "ix_sync_device_cursors_expiry",
        "sync_device_cursors",
        ["expiresAt", "revokedAt"],
        unique=False,
    )
    op.create_index(
        "ix_sync_device_cursors_owner_seen",
        "sync_device_cursors",
        ["ownerId", "lastSeenAt"],
        unique=False,
    )

    op.create_table(
        "sync_idempotency_batches",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ownerId", sa.Integer(), nullable=False),
        sa.Column("deviceId", sa.String(128), nullable=False),
        sa.Column("idempotencyKey", sa.String(128), nullable=False),
        sa.Column("requestHash", sa.String(64), nullable=False),
        sa.Column("protocolVersion", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("requestId", sa.String(64), nullable=False),
        sa.Column("responseStatus", sa.Integer(), nullable=True),
        sa.Column("responsePayload", postgresql.JSONB(), nullable=True),
        sa.Column("expiresAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lastReplayedAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completedAt", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint('length("requestHash") = 64', name="ck_sync_batches_request_hash"),
        sa.CheckConstraint('"status" IN (\'processing\', \'completed\', \'failed\')', name="ck_sync_batches_status"),
        sa.CheckConstraint('"protocolVersion" >= 1', name="ck_sync_batches_protocol_version"),
        sa.CheckConstraint(
            '("status" = \'processing\' AND "completedAt" IS NULL) OR '
            '("status" IN (\'completed\', \'failed\') AND "completedAt" IS NOT NULL '
            'AND "responseStatus" IS NOT NULL AND "responsePayload" IS NOT NULL)',
            name="ck_sync_batches_complete_response",
        ),
        sa.ForeignKeyConstraint(["ownerId"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "ownerId", name="uq_sync_batches_id_owner"),
        sa.UniqueConstraint("ownerId", "deviceId", "idempotencyKey", name="uq_sync_batches_owner_device_key"),
    )
    op.create_index(
        "ix_sync_batches_expiry",
        "sync_idempotency_batches",
        ["expiresAt", "status"],
        unique=False,
    )
    op.create_index(
        "ix_sync_batches_owner_created",
        "sync_idempotency_batches",
        ["ownerId", "createdAt"],
        unique=False,
    )

    op.create_table(
        "sync_operation_results",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("batchId", sa.BigInteger(), nullable=False),
        sa.Column("ownerId", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("operationId", sa.String(36), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("entityType", sa.String(32), nullable=True),
        sa.Column("publicId", sa.String(36), nullable=True),
        sa.Column("acceptedVersion", sa.BigInteger(), nullable=True),
        sa.Column("contentHash", sa.String(64), nullable=True),
        sa.Column("errorCode", sa.String(64), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=True),
        sa.CheckConstraint('"ordinal" >= 0', name="ck_sync_operation_results_ordinal"),
        sa.CheckConstraint(
            '"status" IN (\'applied\', \'noop\', \'conflict\', \'rejected\', \'dependency_failed\')',
            name="ck_sync_operation_results_status",
        ),
        sa.ForeignKeyConstraint(
            ["batchId", "ownerId"],
            ["sync_idempotency_batches.id", "sync_idempotency_batches.ownerId"],
            ondelete="CASCADE",
            name="fk_sync_operation_results_batch_owner",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batchId", "ordinal", name="uq_sync_operation_results_batch_ordinal"),
        sa.UniqueConstraint("ownerId", "operationId", name="uq_sync_operation_results_owner_operation"),
    )
    op.create_index(
        "ix_sync_operation_results_operation",
        "sync_operation_results",
        ["operationId"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_sync_operation_results_operation", table_name="sync_operation_results")
    op.drop_table("sync_operation_results")
    op.drop_index("ix_sync_batches_owner_created", table_name="sync_idempotency_batches")
    op.drop_index("ix_sync_batches_expiry", table_name="sync_idempotency_batches")
    op.drop_table("sync_idempotency_batches")
    op.drop_index("ix_sync_device_cursors_owner_seen", table_name="sync_device_cursors")
    op.drop_index("ix_sync_device_cursors_expiry", table_name="sync_device_cursors")
    op.drop_table("sync_device_cursors")
    op.drop_index("ix_sync_change_log_compaction", table_name="sync_change_log")
    op.drop_index("ix_sync_change_log_pull", table_name="sync_change_log")
    op.drop_table("sync_change_log")
    op.drop_index("ix_sync_entity_versions_entity_created", table_name="sync_entity_versions")
    op.drop_table("sync_entity_versions")
    op.drop_table("sync_streams")
    op.drop_index("ix_sync_entities_owner_type_tombstone", table_name="sync_entities")
    op.drop_index("ix_sync_entities_owner_scope_updated", table_name="sync_entities")
    op.drop_table("sync_entities")
