"""dual auth tokens and audit log

Revision ID: 20260804_0016
Revises: 20260702_0015
Create Date: 2026-08-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260804_0016"
down_revision: str | None = "20260702_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("auth_tokens", sa.Column("tokenType", sa.String(16), nullable=False, server_default="session"))
    op.add_column("auth_tokens", sa.Column("deviceId", sa.String(128), nullable=True))
    op.add_column("auth_tokens", sa.Column("deviceName", sa.String(120), nullable=True))
    op.add_column("auth_tokens", sa.Column("ipAddress", sa.String(64), nullable=True))
    op.add_column("auth_tokens", sa.Column("userAgent", sa.String(512), nullable=True))
    op.add_column("auth_tokens", sa.Column("lastSeenAt", sa.DateTime(timezone=True), nullable=True))
    op.add_column("auth_tokens", sa.Column("refreshTokenId", sa.String(128), nullable=True))
    op.create_index("ix_auth_tokens_deviceId", "auth_tokens", ["deviceId"], unique=False)
    op.create_index("ix_auth_tokens_refreshTokenId", "auth_tokens", ["refreshTokenId"], unique=False)

    op.create_table(
        "auth_audit_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("userId", sa.Integer(), nullable=False),
        sa.Column("deviceId", sa.String(128), nullable=True),
        sa.Column("event", sa.String(32), nullable=False),
        sa.Column("ipAddress", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["userId"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_auth_audit_log_userId", "auth_audit_log", ["userId"], unique=False)
    op.create_index("ix_auth_audit_log_event", "auth_audit_log", ["event"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_auth_audit_log_event", table_name="auth_audit_log")
    op.drop_index("ix_auth_audit_log_userId", table_name="auth_audit_log")
    op.drop_table("auth_audit_log")
    op.drop_index("ix_auth_tokens_refreshTokenId", table_name="auth_tokens")
    op.drop_index("ix_auth_tokens_deviceId", table_name="auth_tokens")
    op.drop_column("auth_tokens", "refreshTokenId")
    op.drop_column("auth_tokens", "lastSeenAt")
    op.drop_column("auth_tokens", "userAgent")
    op.drop_column("auth_tokens", "ipAddress")
    op.drop_column("auth_tokens", "deviceName")
    op.drop_column("auth_tokens", "deviceId")
    op.drop_column("auth_tokens", "tokenType")
