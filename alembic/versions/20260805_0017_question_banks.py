"""add shared question banks

Revision ID: 20260805_0017
Revises: 20260804_0016
Create Date: 2026-08-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260805_0017"
down_revision: str | None = "20260804_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "question_banks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("publicId", sa.String(36), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(1000), nullable=False),
        sa.Column("ownerId", sa.Integer(), nullable=True),
        sa.Column("visibility", sa.String(16), nullable=False),
        sa.Column("latestVersion", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["ownerId"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("publicId"),
    )
    op.create_index("ix_question_banks_publicId", "question_banks", ["publicId"], unique=False)
    op.create_index("ix_question_banks_ownerId", "question_banks", ["ownerId"], unique=False)
    op.create_index("ix_question_banks_visibility", "question_banks", ["visibility"], unique=False)

    op.create_table(
        "question_bank_items",
        sa.Column("bankId", sa.Integer(), nullable=False),
        sa.Column("questionId", sa.Integer(), nullable=False),
        sa.Column("addedBy", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["bankId"], ["question_banks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["questionId"], ["questions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["addedBy"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("bankId", "questionId"),
    )
    op.create_index("ix_question_bank_items_bankId", "question_bank_items", ["bankId"], unique=False)

    op.create_table(
        "question_bank_members",
        sa.Column("bankId", sa.Integer(), nullable=False),
        sa.Column("userId", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["bankId"], ["question_banks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["userId"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("bankId", "userId"),
    )
    op.create_index("ix_question_bank_members_bankId", "question_bank_members", ["bankId"], unique=False)

    op.create_table(
        "bank_publications",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("publicId", sa.String(36), nullable=False),
        sa.Column("bankId", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", postgresql.JSONB(), nullable=False),
        sa.Column("createdBy", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["bankId"], ["question_banks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["createdBy"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bankId", "version", name="uq_bank_publications_bank_version"),
        sa.UniqueConstraint("publicId"),
    )
    op.create_index("ix_bank_publications_publicId", "bank_publications", ["publicId"], unique=False)
    op.create_index("ix_bank_publications_bankId", "bank_publications", ["bankId"], unique=False)
    op.create_index("ix_bank_publications_createdBy", "bank_publications", ["createdBy"], unique=False)

    op.create_table(
        "bank_subscriptions",
        sa.Column("bankId", sa.Integer(), nullable=False),
        sa.Column("userId", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["bankId"], ["question_banks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["userId"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("bankId", "userId"),
    )
    op.create_index("ix_bank_subscriptions_bankId", "bank_subscriptions", ["bankId"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_bank_subscriptions_bankId", table_name="bank_subscriptions")
    op.drop_table("bank_subscriptions")
    op.drop_index("ix_bank_publications_createdBy", table_name="bank_publications")
    op.drop_index("ix_bank_publications_bankId", table_name="bank_publications")
    op.drop_index("ix_bank_publications_publicId", table_name="bank_publications")
    op.drop_table("bank_publications")
    op.drop_index("ix_question_bank_members_bankId", table_name="question_bank_members")
    op.drop_table("question_bank_members")
    op.drop_index("ix_question_bank_items_bankId", table_name="question_bank_items")
    op.drop_table("question_bank_items")
    op.drop_index("ix_question_banks_visibility", table_name="question_banks")
    op.drop_index("ix_question_banks_ownerId", table_name="question_banks")
    op.drop_index("ix_question_banks_publicId", table_name="question_banks")
    op.drop_table("question_banks")
