from __future__ import annotations

import hashlib
import secrets
from typing import cast

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import Depends, HTTPException, Request, status

from testpaper_backend.config import get_auth_cookie_name
from testpaper_backend.db import AuthTokenRow, SessionLocal, UserRow
from testpaper_backend.schemas import ROLE_PERMISSIONS, Permission, TokenType, UserEntity, UserRole
from testpaper_backend.time_utils import as_aware_utc, now_utc

_ph = PasswordHasher()


def password_hash(password: str) -> str:
    return _ph.hash(password)


def _verify_pbkdf2(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations_text, salt, expected = stored_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), int(iterations_text)).hex()
        return secrets.compare_digest(digest, expected)
    except (ValueError, TypeError):
        return False


def verify_password(password: str, stored_hash: str) -> tuple[bool, bool]:
    if stored_hash.startswith("$argon2"):
        try:
            return _ph.verify(stored_hash, password), False
        except VerificationError:
            return False, False
    if _verify_pbkdf2(password, stored_hash):
        return True, True
    return False, False


def permissions_for_role(role: UserRole | str) -> list[Permission]:
    normalized_role = role if isinstance(role, UserRole) else UserRole(role)
    return sorted(ROLE_PERMISSIONS[normalized_role])


def user_row_to_entity(row: UserRow) -> UserEntity:
    role = UserRole(row.role)
    return UserEntity(
        id=row.id,
        publicId=row.public_id,
        username=row.username,
        displayName=row.display_name,
        role=role,
        permissions=permissions_for_role(role),
        isActive=row.is_active,
        avatarUrl=row.avatar_url,
        createdAt=row.created_at,
        updatedAt=row.updated_at,
    )


def has_permission(user: UserEntity, permission: Permission) -> bool:
    return permission in set(user.permissions)


def auth_error(code: str = "UNAUTHORIZED", message: str = "Authentication required") -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": code, "message": message})


def forbidden_error(permission: Permission) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"code": "FORBIDDEN", "message": f"Missing permission: {permission}"},
    )


def get_request_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() == "bearer" and token:
        return token
    return request.cookies.get(get_auth_cookie_name())


def get_user_from_token(token: str | None) -> UserEntity:
    if not token:
        raise auth_error()

    with SessionLocal() as session:
        token_row = cast(AuthTokenRow | None, session.get(AuthTokenRow, token))
        if token_row is None:
            raise auth_error("INVALID_TOKEN", "Invalid or expired token")
        if token_row.token_type == TokenType.refresh.value:
            raise auth_error("INVALID_TOKEN", "Refresh token cannot be used as an access credential")
        if as_aware_utc(token_row.expires_at) <= now_utc():
            session.delete(token_row)
            session.commit()
            raise auth_error("TOKEN_EXPIRED", "Token has expired")
        user_row = cast(UserRow | None, session.get(UserRow, token_row.user_id))
        if user_row is None or not user_row.is_active:
            raise auth_error("ACCOUNT_DISABLED", "Account is disabled")
        return user_row_to_entity(user_row)


def get_current_user(request: Request) -> UserEntity:
    return get_user_from_token(get_request_token(request))


CurrentUserDependency = Depends(get_current_user)


def require_permission(permission: Permission):
    def dependency(current_user: UserEntity = CurrentUserDependency) -> UserEntity:
        if not has_permission(current_user, permission):
            raise forbidden_error(permission)
        return current_user

    return dependency
