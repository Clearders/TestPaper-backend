"""add immutable sync conflict and resolution records

Revision ID: 20260813_0024
Revises: 20260813_0023
Create Date: 2026-08-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260813_0024"
down_revision: str | None = "20260813_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sync_conflicts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("publicId", sa.String(36), nullable=False),
        sa.Column("ownerId", sa.Integer(), nullable=False),
        sa.Column("entityId", sa.BigInteger(), nullable=False),
        sa.Column("entityType", sa.String(32), nullable=False),
        sa.Column("origin", sa.String(24), nullable=False),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("baseSnapshot", postgresql.JSONB(), nullable=True),
        sa.Column("localSnapshot", postgresql.JSONB(), nullable=False),
        sa.Column("cloudSnapshot", postgresql.JSONB(), nullable=False),
        sa.Column("detectedAt", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("\"entityType\" IN ('question', 'paper', 'draft')", name="ck_sync_conflicts_entity_type"),
        sa.CheckConstraint("\"origin\" = 'personalSync'", name="ck_sync_conflicts_origin"),
        sa.CheckConstraint(
            "\"reason\" IN ('concurrentCreate', 'divergentContent', 'tombstoneDivergence', 'restoreDivergence', 'renameDivergence')",
            name="ck_sync_conflicts_reason",
        ),
        sa.CheckConstraint(
            '("reason" = \'concurrentCreate\' AND "baseSnapshot" IS NULL) OR '
            '("reason" <> \'concurrentCreate\' AND "baseSnapshot" IS NOT NULL)',
            name="ck_sync_conflicts_baseline",
        ),
        sa.CheckConstraint("jsonb_typeof(\"localSnapshot\") = 'object'", name="ck_sync_conflicts_local_snapshot"),
        sa.CheckConstraint("jsonb_typeof(\"cloudSnapshot\") = 'object'", name="ck_sync_conflicts_cloud_snapshot"),
        sa.CheckConstraint(
            '"baseSnapshot" IS NULL OR jsonb_typeof("baseSnapshot") = \'object\'',
            name="ck_sync_conflicts_base_snapshot",
        ),
        sa.ForeignKeyConstraint(
            ["entityId", "ownerId"],
            ["sync_entities.id", "sync_entities.ownerId"],
            ondelete="RESTRICT",
            name="fk_sync_conflicts_entity_owner",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "ownerId", name="uq_sync_conflicts_id_owner"),
        sa.UniqueConstraint("ownerId", "publicId", name="uq_sync_conflicts_owner_public_id"),
    )
    op.create_index(
        "ix_sync_conflicts_owner_entity_detected",
        "sync_conflicts",
        ["ownerId", "entityId", "detectedAt"],
    )

    op.create_table(
        "sync_conflict_resolutions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("publicId", sa.String(36), nullable=False),
        sa.Column("conflictId", sa.BigInteger(), nullable=False),
        sa.Column("ownerId", sa.Integer(), nullable=False),
        sa.Column("operationId", sa.String(36), nullable=False),
        sa.Column("action", sa.String(24), nullable=False),
        sa.Column("actorDeviceId", sa.String(128), nullable=False),
        sa.Column("acceptedVersionId", sa.BigInteger(), nullable=False),
        sa.Column("resultSnapshot", postgresql.JSONB(), nullable=False),
        sa.Column("newEntityPublicId", sa.String(36), nullable=True),
        sa.Column("undoesResolutionId", sa.BigInteger(), nullable=True),
        sa.Column("resolvedAt", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "\"action\" IN ('keepLocal', 'useCloud', 'saveCopy', 'manualMerge', 'restoreVersion', 'undo')",
            name="ck_sync_conflict_resolutions_action",
        ),
        sa.CheckConstraint(
            '("action" = \'saveCopy\' AND "newEntityPublicId" IS NOT NULL) OR ("action" <> \'saveCopy\' AND "newEntityPublicId" IS NULL)',
            name="ck_sync_conflict_resolutions_copy_link",
        ),
        sa.CheckConstraint(
            '("action" = \'undo\' AND "undoesResolutionId" IS NOT NULL) OR ("action" <> \'undo\' AND "undoesResolutionId" IS NULL)',
            name="ck_sync_conflict_resolutions_undo_link",
        ),
        sa.CheckConstraint("jsonb_typeof(\"resultSnapshot\") = 'object'", name="ck_sync_conflict_resolutions_snapshot"),
        sa.ForeignKeyConstraint(
            ["conflictId", "ownerId"],
            ["sync_conflicts.id", "sync_conflicts.ownerId"],
            ondelete="RESTRICT",
            name="fk_sync_conflict_resolutions_conflict_owner",
        ),
        sa.ForeignKeyConstraint(
            ["undoesResolutionId", "ownerId"],
            ["sync_conflict_resolutions.id", "sync_conflict_resolutions.ownerId"],
            ondelete="RESTRICT",
            name="fk_sync_conflict_resolutions_undo_owner",
        ),
        sa.ForeignKeyConstraint(["acceptedVersionId"], ["sync_entity_versions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "ownerId", name="uq_sync_conflict_resolutions_id_owner"),
        sa.UniqueConstraint("ownerId", "publicId", name="uq_sync_conflict_resolutions_owner_public_id"),
        sa.UniqueConstraint("ownerId", "operationId", name="uq_sync_conflict_resolutions_owner_operation"),
        sa.UniqueConstraint("undoesResolutionId", name="uq_sync_conflict_resolutions_single_undo"),
    )
    op.create_index(
        "ix_sync_conflict_resolutions_conflict_resolved",
        "sync_conflict_resolutions",
        ["conflictId", "resolvedAt"],
    )

    op.execute(
        sa.text(
            """
            CREATE FUNCTION reject_sync_conflict_audit_mutation() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = '55000';
            END;
            $$ LANGUAGE plpgsql;

            CREATE TRIGGER trg_sync_conflicts_append_only
            BEFORE UPDATE OR DELETE ON sync_conflicts
            FOR EACH ROW EXECUTE FUNCTION reject_sync_conflict_audit_mutation();

            CREATE TRIGGER trg_sync_conflict_resolutions_append_only
            BEFORE UPDATE OR DELETE ON sync_conflict_resolutions
            FOR EACH ROW EXECUTE FUNCTION reject_sync_conflict_audit_mutation();

            CREATE TRIGGER trg_sync_entity_versions_append_only
            BEFORE UPDATE OR DELETE ON sync_entity_versions
            FOR EACH ROW EXECUTE FUNCTION reject_sync_conflict_audit_mutation();

            CREATE TRIGGER trg_sync_change_log_append_only
            BEFORE UPDATE OR DELETE ON sync_change_log
            FOR EACH ROW EXECUTE FUNCTION reject_sync_conflict_audit_mutation();
            """
        )
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_sync_change_log_append_only ON sync_change_log")
    op.execute("DROP TRIGGER IF EXISTS trg_sync_entity_versions_append_only ON sync_entity_versions")
    op.execute("DROP TRIGGER IF EXISTS trg_sync_conflict_resolutions_append_only ON sync_conflict_resolutions")
    op.execute("DROP TRIGGER IF EXISTS trg_sync_conflicts_append_only ON sync_conflicts")
    op.execute("DROP FUNCTION IF EXISTS reject_sync_conflict_audit_mutation()")
    op.drop_index("ix_sync_conflict_resolutions_conflict_resolved", table_name="sync_conflict_resolutions")
    op.drop_table("sync_conflict_resolutions")
    op.drop_index("ix_sync_conflicts_owner_entity_detected", table_name="sync_conflicts")
    op.drop_table("sync_conflicts")
