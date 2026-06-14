from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class UserRole(StrEnum):
    admin = "admin"
    teacher = "teacher"
    viewer = "viewer"


Permission = Literal[
    "questions:read",
    "questions:write",
    "questions:delete",
    "answers:read",
    "papers:read",
    "papers:write",
    "users:manage",
]

ROLE_PERMISSIONS: dict[UserRole, set[Permission]] = {
    UserRole.admin: {
        "questions:read",
        "questions:write",
        "questions:delete",
        "answers:read",
        "papers:read",
        "papers:write",
        "users:manage",
    },
    UserRole.teacher: {
        "questions:read",
        "questions:write",
        "answers:read",
        "papers:read",
        "papers:write",
    },
    UserRole.viewer: {
        "questions:read",
        "papers:read",
    },
}


def _validate_password_complexity(v: str) -> str:
    if not any(c.isalpha() for c in v):
        raise ValueError("Password must contain at least one letter")
    if not any(c.isdigit() for c in v):
        raise ValueError("Password must contain at least one digit")
    return v


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=64)
    displayName: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def check_password_complexity(cls, v: str) -> str:
        return _validate_password_complexity(v)

    @model_validator(mode="after")
    def normalize_register_request(self):
        self.username = self.username.strip().lower()
        self.displayName = self.displayName.strip()
        return self


class UserEntity(BaseModel):
    id: int
    publicId: str
    username: str
    displayName: str
    role: UserRole
    permissions: list[Permission]
    isActive: bool
    avatarUrl: str | None = None
    createdAt: datetime
    updatedAt: datetime


class AuthSession(BaseModel):
    expiresAt: datetime
    user: UserEntity


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=64)
    displayName: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=128)
    role: UserRole = UserRole.viewer
    isActive: bool = True

    @field_validator("password")
    @classmethod
    def check_password_complexity(cls, v: str) -> str:
        return _validate_password_complexity(v)

    @model_validator(mode="after")
    def normalize_user_create(self):
        self.username = self.username.strip().lower()
        self.displayName = self.displayName.strip()
        return self


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    displayName: str | None = Field(default=None, min_length=1, max_length=120)
    password: str | None = Field(default=None, min_length=8, max_length=128)
    role: UserRole | None = None
    isActive: bool | None = None

    @field_validator("password")
    @classmethod
    def check_password_complexity(cls, v: str | None) -> str | None:
        if v is not None:
            return _validate_password_complexity(v)
        return v

    @model_validator(mode="after")
    def normalize_user_update(self):
        if self.displayName is not None:
            self.displayName = self.displayName.strip()
        return self


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str | None = Field(default=None, min_length=3, max_length=64)
    displayName: str | None = Field(default=None, min_length=1, max_length=120)

    @model_validator(mode="after")
    def check_at_least_one_field(self):
        if self.username is None and self.displayName is None:
            raise ValueError("At least one of username or displayName must be provided")
        return self

    @model_validator(mode="after")
    def normalize_profile_update(self):
        if self.username is not None:
            self.username = self.username.strip().lower()
        if self.displayName is not None:
            self.displayName = self.displayName.strip()
        return self


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    currentPassword: str = Field(min_length=1)
    newPassword: str = Field(min_length=8, max_length=128)

    @field_validator("newPassword")
    @classmethod
    def check_password_complexity(cls, v: str) -> str:
        return _validate_password_complexity(v)


class ImageUploadPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(min_length=1)
    data: str = Field(min_length=1)
    mimeType: str = Field(default="image/png")


class ImageUploadResponse(BaseModel):
    url: str
    filename: str
    mimeType: str
