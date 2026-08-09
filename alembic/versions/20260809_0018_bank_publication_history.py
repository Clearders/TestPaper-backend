"""retain bank publication history and pin subscriptions

Revision ID: 20260809_0018
Revises: 20260805_0017
Create Date: 2026-08-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260809_0018"
down_revision: str | None = "20260805_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("bank_publications", sa.Column("withdrawnAt", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_bank_publications_withdrawnAt", "bank_publications", ["withdrawnAt"], unique=False)

    op.add_column("bank_subscriptions", sa.Column("publicationId", sa.Integer(), nullable=True))
    op.add_column("bank_subscriptions", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_bank_subscriptions_publicationId_bank_publications",
        "bank_subscriptions",
        "bank_publications",
        ["publicationId"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_bank_subscriptions_publicationId", "bank_subscriptions", ["publicationId"], unique=False)

    op.execute(
        sa.text(
            "UPDATE bank_subscriptions AS subscription "
            'SET "publicationId" = ('
            "  SELECT publication.id FROM bank_publications AS publication "
            '  WHERE publication."bankId" = subscription."bankId" '
            "  ORDER BY publication.version DESC LIMIT 1"
            ")"
        )
    )
    op.execute(sa.text("UPDATE bank_subscriptions SET updated_at = created_at"))
    op.alter_column("bank_subscriptions", "updated_at", nullable=False)


def downgrade() -> None:
    op.drop_index("ix_bank_subscriptions_publicationId", table_name="bank_subscriptions")
    op.drop_constraint(
        "fk_bank_subscriptions_publicationId_bank_publications",
        "bank_subscriptions",
        type_="foreignkey",
    )
    op.drop_column("bank_subscriptions", "updated_at")
    op.drop_column("bank_subscriptions", "publicationId")
    op.drop_index("ix_bank_publications_withdrawnAt", table_name="bank_publications")
    op.drop_column("bank_publications", "withdrawnAt")
