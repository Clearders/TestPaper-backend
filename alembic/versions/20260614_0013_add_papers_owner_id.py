"""add ownerId to papers

Revision ID: 20260614_0013
Revises: 20260612_0012
Create Date: 2026-06-14 00:01:00
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "20260614_0013"
down_revision: Union[str, None] = "20260612_0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "papers",
        sa.Column("ownerId", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index(op.f("ix_papers_ownerId"), "papers", ["ownerId"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_papers_ownerId"), table_name="papers")
    op.drop_column("papers", "ownerId")
