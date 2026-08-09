"""hash opaque auth tokens and add refresh-family revocation state

Revision ID: 20260809_0019
Revises: 20260809_0018
Create Date: 2026-08-09
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260809_0019"
down_revision: str | None = "20260809_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def upgrade() -> None:
    op.add_column("auth_tokens", sa.Column("familyId", sa.String(64), nullable=True))
    op.add_column("auth_tokens", sa.Column("revokedAt", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_auth_tokens_familyId", "auth_tokens", ["familyId"], unique=False)
    op.create_index("ix_auth_tokens_revokedAt", "auth_tokens", ["revokedAt"], unique=False)

    # Convert every existing bearer secret in-place. The raw value is never
    # copied to a new table or logged. Existing clients continue presenting the
    # raw token while application lookups hash it before querying.
    connection = op.get_bind()
    rows = connection.execute(
        sa.text('SELECT token, "tokenType", "refreshTokenId" FROM auth_tokens')
    ).fetchall()
    for raw_token, token_type, raw_refresh_token in rows:
        family_source = raw_refresh_token or raw_token
        family_id = None if token_type == "session" else f"legacy-{_digest(family_source)[:32]}"
        connection.execute(
            sa.text(
                'UPDATE auth_tokens SET token = :digest, "refreshTokenId" = :refresh_digest, '
                '"familyId" = :family_id WHERE token = :raw_token'
            ),
            {
                "digest": _digest(raw_token),
                "refresh_digest": _digest(raw_refresh_token) if raw_refresh_token else None,
                "family_id": family_id,
                "raw_token": raw_token,
            },
        )


def downgrade() -> None:
    # Token hashing is intentionally irreversible. A rollback safely signs out
    # every client instead of turning database digests into usable bearer tokens
    # under older application code.
    op.execute(sa.text("DELETE FROM auth_tokens"))
    op.drop_index("ix_auth_tokens_revokedAt", table_name="auth_tokens")
    op.drop_index("ix_auth_tokens_familyId", table_name="auth_tokens")
    op.drop_column("auth_tokens", "revokedAt")
    op.drop_column("auth_tokens", "familyId")
