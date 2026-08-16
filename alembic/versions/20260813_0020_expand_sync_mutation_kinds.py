"""expand sync mutation kinds to the complete v1 contract

Revision ID: 20260813_0020
Revises: 20260813_0019
Create Date: 2026-08-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260813_0020"
down_revision: str | None = "20260813_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "ALTER TABLE sync_entity_versions DROP CONSTRAINT ck_sync_entity_versions_mutation_kind, "
            "ADD CONSTRAINT ck_sync_entity_versions_mutation_kind "
            'CHECK ("mutationKind" IN (\'create\', \'update\', \'delete\', \'restore\', \'rename\', \'attach\', \'detach\'))'
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            'UPDATE sync_entity_versions SET "mutationKind" = \'update\' '
            'WHERE "mutationKind" IN (\'rename\', \'attach\', \'detach\')'
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE sync_entity_versions DROP CONSTRAINT ck_sync_entity_versions_mutation_kind, "
            "ADD CONSTRAINT ck_sync_entity_versions_mutation_kind "
            'CHECK ("mutationKind" IN (\'create\', \'update\', \'delete\', \'restore\'))'
        )
    )
