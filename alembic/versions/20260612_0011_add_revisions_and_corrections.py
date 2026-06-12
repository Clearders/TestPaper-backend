"""add question_revisions and question_corrections tables

Revision ID: 20260612_0011
Revises: 20260611_0010
Create Date: 2026-06-12

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260612_0011"
down_revision: str | None = "20260611_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "question_revisions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("question_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("patch", postgresql.JSONB(), nullable=False),
        sa.Column("changeSummary", sa.String(500), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["question_id"], ["questions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_question_revisions_question_id", "question_revisions", ["question_id"])
    op.create_index("ix_question_revisions_user_id", "question_revisions", ["user_id"])

    op.create_table(
        "question_corrections",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("question_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("message", sa.String(1000), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["question_id"], ["questions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_question_corrections_question_id", "question_corrections", ["question_id"])
    op.create_index("ix_question_corrections_user_id", "question_corrections", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_question_corrections_user_id", table_name="question_corrections")
    op.drop_index("ix_question_corrections_question_id", table_name="question_corrections")
    op.drop_table("question_corrections")
    op.drop_index("ix_question_revisions_user_id", table_name="question_revisions")
    op.drop_index("ix_question_revisions_question_id", table_name="question_revisions")
    op.drop_table("question_revisions")
