"""add owner_id, images, and new question types

Revision ID: 20260508_0002
Revises: 20260507_0001
Create Date: 2026-05-08 00:01:00
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260508_0002"
down_revision: Union[str, None] = "20260507_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "questions",
        sa.Column("ownerId", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index(op.f("ix_questions_ownerId"), "questions", ["ownerId"], unique=False)

    op.add_column(
        "questions",
        sa.Column("images", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("questions", "images")
    op.drop_index(op.f("ix_questions_ownerId"), table_name="questions")
    op.drop_column("questions", "ownerId")
