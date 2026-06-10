"""split choice type into single_choice and multiple_choice, migrate answer column to JSONB

Revision ID: 20260611_0009
Revises: 20260609_0008
Create Date: 2026-06-11

"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260611_0009"
down_revision: str | None = "20260609_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE questions SET type = 'single_choice' WHERE type = 'choice'")

    op.execute("ALTER TABLE questions ADD COLUMN answer_jsonb JSONB")
    op.execute("UPDATE questions SET answer_jsonb = to_jsonb(answer)")
    op.execute("ALTER TABLE questions DROP COLUMN answer")
    op.execute("ALTER TABLE questions RENAME COLUMN answer_jsonb TO answer")


def downgrade() -> None:
    op.execute("ALTER TABLE questions ADD COLUMN answer_text VARCHAR")
    op.execute("UPDATE questions SET answer_text = answer ->> 0 WHERE jsonb_typeof(answer) = 'string'")
    op.execute("UPDATE questions SET answer_text = answer::text WHERE jsonb_typeof(answer) = 'array'")
    op.execute("ALTER TABLE questions DROP COLUMN answer")
    op.execute("ALTER TABLE questions RENAME COLUMN answer_text TO answer")
    op.execute("UPDATE questions SET type = 'choice' WHERE type IN ('single_choice', 'multiple_choice')")
