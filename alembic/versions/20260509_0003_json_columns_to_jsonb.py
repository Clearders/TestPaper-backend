"""convert JSON columns to JSONB

Revision ID: 20260509_0003
Revises: 20260508_0002
Create Date: 2026-05-09 00:03:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260509_0003"
down_revision: str | None = "20260508_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


JSONB_COLUMNS = (
    ("tags", False),
    ("options", True),
    ("essay_blank_space", True),
    ("images", False),
)


def upgrade() -> None:
    for column_name, nullable in JSONB_COLUMNS:
        if column_name == "images":
            op.alter_column(
                "questions",
                column_name,
                server_default=None,
                existing_type=sa.JSON(),
                existing_nullable=nullable,
            )
        op.alter_column(
            "questions",
            column_name,
            existing_type=sa.JSON(),
            type_=postgresql.JSONB(),
            existing_nullable=nullable,
            postgresql_using=f"{column_name}::jsonb",
        )
        if column_name == "images":
            op.alter_column(
                "questions",
                column_name,
                server_default=sa.text("'[]'::jsonb"),
                existing_type=postgresql.JSONB(),
                existing_nullable=nullable,
            )


def downgrade() -> None:
    # Revision 20260508_0002 already represents these question columns as JSONB.
    # A one-step downgrade must therefore preserve JSONB and the images default.
    op.alter_column(
        "questions",
        "images",
        server_default=sa.text("'[]'::jsonb"),
        existing_type=postgresql.JSONB(),
        existing_nullable=False,
    )
