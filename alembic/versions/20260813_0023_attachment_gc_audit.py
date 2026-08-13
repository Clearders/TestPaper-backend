"""add append-only attachment garbage collection audit

Revision ID: 20260813_0023
Revises: 20260813_0022
Create Date: 2026-08-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260813_0023"
down_revision: str | None = "20260813_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "attachment_gc_audit",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("targetKind", sa.String(16), nullable=False),
        sa.Column("targetId", sa.String(64), nullable=False),
        sa.Column("contentHash", sa.String(64), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('expired_upload_deleted', 'blob_metadata_deleted', 'blob_file_deleted', 'blob_file_delete_failed')",
            name="ck_attachment_gc_audit_action",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_attachment_gc_audit_created", "attachment_gc_audit", ["createdAt"])
    op.execute(
        sa.text(
            """
            CREATE FUNCTION reject_attachment_gc_audit_mutation() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'attachment GC audit is append-only' USING ERRCODE = '55000';
            END;
            $$ LANGUAGE plpgsql;

            CREATE TRIGGER trg_attachment_gc_audit_append_only
            BEFORE UPDATE OR DELETE ON attachment_gc_audit
            FOR EACH ROW EXECUTE FUNCTION reject_attachment_gc_audit_mutation();
            """
        )
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_attachment_gc_audit_append_only ON attachment_gc_audit")
    op.execute("DROP FUNCTION IF EXISTS reject_attachment_gc_audit_mutation()")
    op.drop_index("ix_attachment_gc_audit_created", table_name="attachment_gc_audit")
    op.drop_table("attachment_gc_audit")
