"""persist exact conflict-resolution replay identity

Revision ID: 20260813_0025
Revises: 20260813_0024
Create Date: 2026-08-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260813_0025"
down_revision: str | None = "20260813_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE sync_conflicts DROP CONSTRAINT ck_sync_conflicts_entity_type")
    op.execute(
        "ALTER TABLE sync_conflicts ADD CONSTRAINT ck_sync_conflicts_entity_type "
        "CHECK (\"entityType\" IN ('question', 'paper', 'draft', 'attachment', 'comment', 'favorite', 'setting'))"
    )
    op.add_column("sync_conflict_resolutions", sa.Column("requestHash", sa.String(64), nullable=True))
    op.execute(
        sa.text(
            'UPDATE sync_conflict_resolutions SET "requestHash" = '
            "repeat(md5(concat(\"operationId\", '-legacy')), 2)"
        )
    )
    op.create_table(
        "sync_version_restores",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ownerId", sa.Integer(), nullable=False),
        sa.Column("entityId", sa.BigInteger(), nullable=False),
        sa.Column("operationId", sa.String(36), nullable=False),
        sa.Column("requestHash", sa.String(64), nullable=False),
        sa.Column("targetVersion", sa.BigInteger(), nullable=False),
        sa.Column("acceptedVersionId", sa.BigInteger(), nullable=False),
        sa.Column("actorDeviceId", sa.String(128), nullable=False),
        sa.Column("restoredAt", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('length("requestHash") = 64', name="ck_sync_version_restores_request_hash"),
        sa.CheckConstraint('"targetVersion" >= 1', name="ck_sync_version_restores_target_version"),
        sa.ForeignKeyConstraint(
            ["entityId", "ownerId"],
            ["sync_entities.id", "sync_entities.ownerId"],
            ondelete="RESTRICT",
            name="fk_sync_version_restores_entity_owner",
        ),
        sa.ForeignKeyConstraint(["acceptedVersionId"], ["sync_entity_versions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ownerId", "operationId", name="uq_sync_version_restores_owner_operation"),
    )
    op.create_index(
        "ix_sync_version_restores_entity_created",
        "sync_version_restores",
        ["entityId", "restoredAt"],
    )
    op.execute(
        "CREATE TRIGGER trg_sync_version_restores_append_only "
        "BEFORE UPDATE OR DELETE ON sync_version_restores "
        "FOR EACH ROW EXECUTE FUNCTION reject_sync_conflict_audit_mutation()"
    )
    op.alter_column("sync_conflict_resolutions", "requestHash", nullable=False)
    op.execute(
        'ALTER TABLE sync_conflict_resolutions ADD CONSTRAINT '
        'ck_sync_conflict_resolutions_request_hash CHECK (length("requestHash") = 64)'
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_sync_version_restores_append_only ON sync_version_restores")
    op.drop_index("ix_sync_version_restores_entity_created", table_name="sync_version_restores")
    op.drop_table("sync_version_restores")
    op.execute(
        "ALTER TABLE sync_conflict_resolutions DROP CONSTRAINT "
        "ck_sync_conflict_resolutions_request_hash"
    )
    op.drop_column("sync_conflict_resolutions", "requestHash")
    op.execute("ALTER TABLE sync_conflicts DROP CONSTRAINT ck_sync_conflicts_entity_type")
    op.execute(
        "ALTER TABLE sync_conflicts ADD CONSTRAINT ck_sync_conflicts_entity_type "
        "CHECK (\"entityType\" IN ('question', 'paper', 'draft'))"
    )
