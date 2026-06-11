"""replace subject column with subjects JSONB array

Revision ID: 20260611_0010
Revises: 20260611_0009
Create Date: 2026-06-11

"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260611_0010"
down_revision: str | None = "20260611_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute('ALTER TABLE questions ADD COLUMN "subjects" JSONB NOT NULL DEFAULT \'[]\'')
    op.execute('UPDATE questions SET "subjects" = jsonb_build_array("subject") WHERE "subject" IS NOT NULL AND "subject" != \'\'')
    op.execute('ALTER TABLE questions DROP COLUMN "subject"')
    op.execute('CREATE INDEX IF NOT EXISTS ix_questions_subjects_gin ON questions USING gin ("subjects")')
    op.execute('CREATE INDEX IF NOT EXISTS ix_questions_subjects_trgm ON questions USING gin (("subjects"::text) gin_trgm_ops)')


def downgrade() -> None:
    op.execute('ALTER TABLE questions ADD COLUMN "subject" VARCHAR(255)')
    op.execute('UPDATE questions SET "subject" = "subjects"->>0')
    op.execute('ALTER TABLE questions DROP COLUMN "subjects"')
    op.execute('DROP INDEX IF EXISTS ix_questions_subjects_gin')
    op.execute('DROP INDEX IF EXISTS ix_questions_subjects_trgm')
