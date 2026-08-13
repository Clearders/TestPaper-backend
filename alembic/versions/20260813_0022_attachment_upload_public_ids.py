"""add opaque public IDs for attachment upload sessions

Revision ID: 20260813_0022
Revises: 20260813_0021
Create Date: 2026-08-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260813_0022"
down_revision: str | None = "20260813_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("attachment_upload_sessions", sa.Column("publicId", sa.String(36), nullable=True))
    op.execute(
        sa.text(
            'UPDATE attachment_upload_sessions SET "publicId" = '
            "lower(substr(md5(id::text || ':' || \"ownerId\"::text), 1, 8) || '-' || "
            "substr(md5(id::text || ':' || \"ownerId\"::text), 9, 4) || '-4' || "
            "substr(md5(id::text || ':' || \"ownerId\"::text), 14, 3) || '-8' || "
            "substr(md5(id::text || ':' || \"ownerId\"::text), 18, 3) || '-' || "
            "substr(md5(id::text || ':' || \"ownerId\"::text), 21, 12))"
        )
    )
    op.alter_column("attachment_upload_sessions", "publicId", nullable=False)
    op.create_unique_constraint(
        "uq_attachment_upload_sessions_owner_public_id",
        "attachment_upload_sessions",
        ["ownerId", "publicId"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_attachment_upload_sessions_owner_public_id",
        "attachment_upload_sessions",
        type_="unique",
    )
    op.drop_column("attachment_upload_sessions", "publicId")
