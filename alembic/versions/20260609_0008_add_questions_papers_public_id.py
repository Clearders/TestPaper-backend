"""add publicId to questions and papers

Revision ID: 20260609_0008
Revises: 20260609_0007
Create Date: 2026-06-09 00:08:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa

from alembic import op

revision: str = "20260609_0008"
down_revision: str | None = "20260609_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _add_public_id(table: str) -> None:
    op.add_column(table, sa.Column("publicId", sa.String(length=36), nullable=True))
    conn = op.get_bind()
    rows = conn.execute(sa.text(f'SELECT id FROM {table} WHERE "publicId" IS NULL')).fetchall()
    for row in rows:
        conn.execute(
            sa.text(f'UPDATE {table} SET "publicId" = :pid WHERE id = :uid'),
            {"pid": str(uuid4()), "uid": row[0]},
        )
    op.alter_column(table, "publicId", nullable=False)
    op.create_unique_constraint(f"uq_{table}_publicId", table, ["publicId"])
    op.create_index(f"ix_{table}_publicId", table, ["publicId"], unique=False)


def _drop_public_id(table: str) -> None:
    op.drop_index(f"ix_{table}_publicId", table_name=table)
    op.drop_constraint(f"uq_{table}_publicId", table_name=table)
    op.drop_column(table, "publicId")


def upgrade() -> None:
    _add_public_id("questions")
    _add_public_id("papers")


def downgrade() -> None:
    _drop_public_id("papers")
    _drop_public_id("questions")
