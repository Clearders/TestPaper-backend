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
    # Some early installations already used the array-shaped column in the
    # initial revision. Keep this migration valid for both historical layouts.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'questions'
                  AND column_name = 'subjects'
            ) THEN
                ALTER TABLE questions ADD COLUMN "subjects" JSONB NOT NULL DEFAULT '[]';
            END IF;

            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'questions'
                  AND column_name = 'subject'
            ) THEN
                UPDATE questions
                SET "subjects" = jsonb_build_array("subject")
                WHERE "subject" IS NOT NULL AND "subject" != '';
                ALTER TABLE questions DROP COLUMN "subject";
            END IF;
        END $$;
        """
    )
    op.execute('CREATE INDEX IF NOT EXISTS ix_questions_subjects_gin ON questions USING gin ("subjects")')
    op.execute('CREATE INDEX IF NOT EXISTS ix_questions_subjects_trgm ON questions USING gin (("subjects"::text) gin_trgm_ops)')


def downgrade() -> None:
    op.execute('DROP INDEX IF EXISTS ix_questions_subjects_gin')
    op.execute('DROP INDEX IF EXISTS ix_questions_subjects_trgm')
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'questions'
                  AND column_name = 'subject'
            ) THEN
                ALTER TABLE questions ADD COLUMN "subject" VARCHAR(255);
            END IF;

            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'questions'
                  AND column_name = 'subjects'
            ) THEN
                UPDATE questions SET "subject" = "subjects"->>0;
                ALTER TABLE questions DROP COLUMN "subjects";
            END IF;
        END $$;
        """
    )
