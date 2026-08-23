"""allow invariant-guarded sync change-log compaction

Revision ID: 20260823_0026
Revises: 20260809_0019
Create Date: 2026-08-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260823_0026"
down_revision: str | None = "20260809_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_sync_change_log_entity_sequence",
        "sync_change_log",
        ["ownerId", "scope", "entityType", "publicId", "sequence"],
    )
    op.execute("DROP TRIGGER IF EXISTS trg_sync_change_log_append_only ON sync_change_log")
    op.execute(
        sa.text(
            """
            CREATE FUNCTION guard_sync_change_log_mutation() RETURNS trigger AS $$
            BEGIN
                IF TG_OP = 'UPDATE' THEN
                    RAISE EXCEPTION '% is append-only and cannot be updated', TG_TABLE_NAME USING ERRCODE = '55000';
                END IF;

                IF NOT EXISTS (
                    SELECT 1
                    FROM sync_streams AS stream
                    WHERE stream."ownerId" = OLD."ownerId"
                      AND stream.scope = OLD.scope
                      AND OLD.sequence <= stream."retainedFromSequence"
                ) THEN
                    RAISE EXCEPTION '% is append-only above the retained horizon', TG_TABLE_NAME USING ERRCODE = '55000';
                END IF;

                IF NOT EXISTS (
                    SELECT 1
                    FROM sync_change_log AS later
                    WHERE later."ownerId" = OLD."ownerId"
                      AND later.scope = OLD.scope
                      AND later."entityType" = OLD."entityType"
                      AND later."publicId" = OLD."publicId"
                      AND later.sequence > OLD.sequence
                ) THEN
                    RAISE EXCEPTION '% latest entity row is append-only', TG_TABLE_NAME USING ERRCODE = '55000';
                END IF;

                RETURN OLD;
            END;
            $$ LANGUAGE plpgsql;

            CREATE TRIGGER trg_sync_change_log_append_only
            BEFORE UPDATE OR DELETE ON sync_change_log
            FOR EACH ROW EXECUTE FUNCTION guard_sync_change_log_mutation();
            """
        )
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_sync_change_log_append_only ON sync_change_log")
    op.execute("DROP FUNCTION IF EXISTS guard_sync_change_log_mutation()")
    op.execute(
        "CREATE TRIGGER trg_sync_change_log_append_only "
        "BEFORE UPDATE OR DELETE ON sync_change_log "
        "FOR EACH ROW EXECUTE FUNCTION reject_sync_conflict_audit_mutation()"
    )
    op.drop_index("ix_sync_change_log_entity_sequence", table_name="sync_change_log")
