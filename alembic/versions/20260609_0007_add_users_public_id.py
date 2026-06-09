"""add publicId to users

Revision ID: 20260609_0007
Revises: 20260513_0006
Create Date: 2026-06-09 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa

from alembic import op

revision: str = "20260609_0007"
down_revision: str | None = "20260513_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("publicId", sa.String(length=36), nullable=True),
    )
    conn = op.get_bind()
    rows = conn.execute(sa.text('SELECT id FROM users WHERE "publicId" IS NULL')).fetchall()
    for row in rows:
        conn.execute(
            sa.text('UPDATE users SET "publicId" = :pid WHERE id = :uid'),
            {"pid": str(uuid4()), "uid": row[0]},
        )
    op.alter_column("users", "publicId", nullable=False)
    op.create_unique_constraint("uq_users_publicId", "users", ["publicId"])
    op.create_index("ix_users_publicId", "users", ["publicId"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_users_publicId", table_name="users")
    op.drop_constraint("uq_users_publicId", table_name="users")
    op.drop_column("users", "publicId")
