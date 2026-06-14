from __future__ import annotations

from datetime import UTC, datetime

from testpaper_backend.schemas import UserEntity, UserRole
from testpaper_backend.security import (
    has_permission,
    password_hash,
    verify_password,
)


def test_password_hash_and_verify():
    pw = "test_password1"
    hashed = password_hash(pw)
    assert hashed.startswith("$argon2")
    valid, needs_migration = verify_password(pw, hashed)
    assert valid is True
    assert needs_migration is False


def test_verify_wrong_password():
    hashed = password_hash("correct1")
    valid, _ = verify_password("wrong1", hashed)
    assert valid is False


def test_has_permission():
    user = UserEntity(
        id=1,
        publicId="u1",
        username="admin",
        displayName="Admin",
        role=UserRole.admin,
        permissions=sorted([
            "questions:read",
            "questions:write",
            "questions:delete",
            "answers:read",
            "papers:read",
            "papers:write",
            "users:manage",
        ]),
        isActive=True,
        createdAt=datetime(2026, 5, 19, tzinfo=UTC),
        updatedAt=datetime(2026, 5, 19, tzinfo=UTC),
    )
    assert has_permission(user, "questions:read") is True
    assert has_permission(user, "users:manage") is True


def test_password_hash_unique():
    assert password_hash("pw1") != password_hash("pw1")
