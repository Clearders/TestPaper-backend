from __future__ import annotations

from fastapi import HTTPException, status


def user_not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "USER_NOT_FOUND", "message": "User not found"})


def username_exists() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": "USER_ALREADY_EXISTS", "message": "Username already exists"},
    )


def self_modification_forbidden(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": "SELF_MODIFICATION_FORBIDDEN", "message": message},
    )


def self_delete_forbidden() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": "VALIDATION_ERROR", "message": "You cannot delete your own account"},
    )
