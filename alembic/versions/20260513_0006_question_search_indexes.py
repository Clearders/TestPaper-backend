"""add question search indexes

Revision ID: 20260513_0006
Revises: 20260511_0005
Create Date: 2026-05-13 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260513_0006"
down_revision: str | None = "20260511_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE INDEX IF NOT EXISTS ix_questions_text_trgm ON questions USING gin (lower(text) gin_trgm_ops)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_questions_answer_trgm ON questions USING gin (lower(answer) gin_trgm_ops)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_questions_source_trgm ON questions USING gin (lower(coalesce(source, '')) gin_trgm_ops)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_questions_tags_trgm ON questions USING gin (lower(tags::text) gin_trgm_ops)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_questions_options_trgm "
        "ON questions USING gin (lower(coalesce(options::text, '')) gin_trgm_ops)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_questions_tags_gin ON questions USING gin (tags)")
    op.create_index("ix_questions_type", "questions", ["type"], unique=False, if_not_exists=True)
    op.create_index("ix_questions_has_latex", "questions", ["has_latex"], unique=False, if_not_exists=True)
    op.create_index("ix_questions_created_at", "questions", ["created_at"], unique=False, if_not_exists=True)
    op.create_index("ix_questions_updated_at", "questions", ["updated_at"], unique=False, if_not_exists=True)


def downgrade() -> None:
    op.drop_index("ix_questions_updated_at", table_name="questions", if_exists=True)
    op.drop_index("ix_questions_created_at", table_name="questions", if_exists=True)
    op.drop_index("ix_questions_has_latex", table_name="questions", if_exists=True)
    op.drop_index("ix_questions_type", table_name="questions", if_exists=True)
    op.execute("DROP INDEX IF EXISTS ix_questions_tags_gin")
    op.execute("DROP INDEX IF EXISTS ix_questions_options_trgm")
    op.execute("DROP INDEX IF EXISTS ix_questions_tags_trgm")
    op.execute("DROP INDEX IF EXISTS ix_questions_source_trgm")
    op.execute("DROP INDEX IF EXISTS ix_questions_answer_trgm")
    op.execute("DROP INDEX IF EXISTS ix_questions_text_trgm")
