"""disable unchanged demo accounts

Revision ID: 20260614_0014
Revises: 20260614_0013
Create Date: 2026-06-14
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260614_0014"
down_revision: str | None = "20260614_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Older revisions created three public demo accounts with deterministic
    # PBKDF2 salts. Disable only accounts whose hashes are still unchanged.
    op.execute(
        """
        UPDATE users
        SET "isActive" = FALSE, updated_at = CURRENT_TIMESTAMP
        WHERE (username = 'admin' AND "passwordHash" LIKE 'pbkdf2_sha256$120000$testpapers_admin_seed$%')
           OR (username = 'teacher' AND "passwordHash" LIKE 'pbkdf2_sha256$120000$testpapers_teacher_seed$%')
           OR (username = 'viewer' AND "passwordHash" LIKE 'pbkdf2_sha256$120000$testpapers_viewer_seed$%')
        """
    )


def downgrade() -> None:
    # Re-enabling known-password accounts would recreate the vulnerability.
    pass
