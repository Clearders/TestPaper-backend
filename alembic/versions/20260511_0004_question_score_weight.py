"""add question score weight

Revision ID: 20260511_0004
Revises: 20260509_0003
Create Date: 2026-05-11 00:00:00.000000
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260511_0004"
down_revision: str | None = "20260509_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "questions",
        sa.Column("scoreWeight", sa.Float(), nullable=False, server_default="1"),
    )
    op.alter_column("questions", "scoreWeight", server_default=None)


def downgrade() -> None:
    op.drop_column("questions", "scoreWeight")
