"""preserve a pre-boundary change-log anchor per sync entity

Revision ID: 20260823_0027
Revises: 20260823_0026
Create Date: 2026-08-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260823_0027"
down_revision: str | None = "20260823_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION guard_sync_change_log_mutation() RETURNS trigger AS $$
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
                    JOIN sync_streams AS stream
                      ON stream."ownerId" = OLD."ownerId"
                     AND stream.scope = OLD.scope
                    WHERE later."ownerId" = OLD."ownerId"
                      AND later.scope = OLD.scope
                      AND later."entityType" = OLD."entityType"
                      AND later."publicId" = OLD."publicId"
                      AND later.sequence > OLD.sequence
                      AND later.sequence <= stream."retainedFromSequence"
                ) THEN
                    RAISE EXCEPTION '% latest retained entity row is append-only', TG_TABLE_NAME USING ERRCODE = '55000';
                END IF;

                RETURN OLD;
            END;
            $$ LANGUAGE plpgsql;
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION guard_sync_change_log_mutation() RETURNS trigger AS $$
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
            """
        )
    )
