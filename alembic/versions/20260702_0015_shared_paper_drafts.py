"""add shared paper drafts

Revision ID: 20260702_0015
Revises: 20260614_0014
Create Date: 2026-07-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260702_0015"
down_revision: str | None = "20260614_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "paper_drafts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("publicId", sa.String(36), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("ownerId", sa.Integer(), nullable=True),
        sa.Column("state", postgresql.JSONB(), nullable=False),
        sa.Column("reviewStatus", sa.String(32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("updatedBy", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["ownerId"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updatedBy"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("publicId"),
    )
    op.create_index("ix_paper_drafts_publicId", "paper_drafts", ["publicId"], unique=False)
    op.create_index("ix_paper_drafts_ownerId", "paper_drafts", ["ownerId"], unique=False)
    op.create_index("ix_paper_drafts_reviewStatus", "paper_drafts", ["reviewStatus"], unique=False)
    op.create_index("ix_paper_drafts_updatedBy", "paper_drafts", ["updatedBy"], unique=False)

    op.create_table(
        "paper_draft_collaborators",
        sa.Column("draftId", sa.Integer(), nullable=False),
        sa.Column("userId", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["draftId"], ["paper_drafts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["userId"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("draftId", "userId"),
    )

    op.create_table(
        "paper_draft_comments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("publicId", sa.String(36), nullable=False),
        sa.Column("draftId", sa.Integer(), nullable=False),
        sa.Column("questionPublicId", sa.String(36), nullable=True),
        sa.Column("message", sa.String(1000), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("authorId", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["draftId"], ["paper_drafts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["authorId"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("publicId"),
    )
    op.create_index("ix_paper_draft_comments_publicId", "paper_draft_comments", ["publicId"], unique=False)
    op.create_index("ix_paper_draft_comments_draftId", "paper_draft_comments", ["draftId"], unique=False)
    op.create_index("ix_paper_draft_comments_questionPublicId", "paper_draft_comments", ["questionPublicId"], unique=False)
    op.create_index("ix_paper_draft_comments_status", "paper_draft_comments", ["status"], unique=False)
    op.create_index("ix_paper_draft_comments_authorId", "paper_draft_comments", ["authorId"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_paper_draft_comments_authorId", table_name="paper_draft_comments")
    op.drop_index("ix_paper_draft_comments_status", table_name="paper_draft_comments")
    op.drop_index("ix_paper_draft_comments_questionPublicId", table_name="paper_draft_comments")
    op.drop_index("ix_paper_draft_comments_draftId", table_name="paper_draft_comments")
    op.drop_index("ix_paper_draft_comments_publicId", table_name="paper_draft_comments")
    op.drop_table("paper_draft_comments")
    op.drop_table("paper_draft_collaborators")
    op.drop_index("ix_paper_drafts_updatedBy", table_name="paper_drafts")
    op.drop_index("ix_paper_drafts_reviewStatus", table_name="paper_drafts")
    op.drop_index("ix_paper_drafts_ownerId", table_name="paper_drafts")
    op.drop_index("ix_paper_drafts_publicId", table_name="paper_drafts")
    op.drop_table("paper_drafts")
