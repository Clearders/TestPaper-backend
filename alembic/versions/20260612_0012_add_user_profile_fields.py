"""add avatarUrl and lastUsernameChangedAt to users

Revision ID: 20260612_0012
Revises: 20260612_0011
Create Date: 2026-06-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260612_0012"
down_revision: str | None = "20260612_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("avatarUrl", sa.String(512), nullable=True))
    op.add_column("users", sa.Column("lastUsernameChangedAt", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "lastUsernameChangedAt")
    op.drop_column("users", "avatarUrl")
