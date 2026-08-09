from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class BankVisibility(StrEnum):
    private = "private"
    team = "team"
    public = "public"


class BankRole(StrEnum):
    viewer = "viewer"
    editor = "editor"


class BankAccessRole(StrEnum):
    owner = "owner"
    admin = "admin"
    editor = "editor"
    viewer = "viewer"


class BankListScope(StrEnum):
    visible = "visible"
    owned = "owned"
    subscribed = "subscribed"
    public = "public"


class BankUserRef(BaseModel):
    publicId: str
    username: str
    displayName: str


class BankMemberEntity(BaseModel):
    user: BankUserRef
    role: BankRole
    createdAt: datetime
    updatedAt: datetime


class QuestionBankSummary(BaseModel):
    id: int
    publicId: str
    name: str
    description: str
    visibility: BankVisibility
    owner: BankUserRef | None = None
    accessRole: BankAccessRole
    version: int | None = None
    itemCount: int = 0
    memberCount: int = 0
    subscriberCount: int = 0
    isSubscribed: bool = False
    subscribedVersion: int | None = None
    hasUpdate: bool = False
    createdAt: datetime
    updatedAt: datetime


class QuestionBankEntity(QuestionBankSummary):
    members: list[BankMemberEntity] = Field(default_factory=list)


class BankCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    visibility: BankVisibility = BankVisibility.private

    @model_validator(mode="after")
    def normalize_create(self):
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("name is required")
        self.description = self.description.strip()
        return self


class BankUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    visibility: BankVisibility | None = None

    @model_validator(mode="after")
    def normalize_update(self):
        if self.name is None and self.description is None and self.visibility is None:
            raise ValueError("At least one bank field must be updated")
        if self.name is not None:
            self.name = self.name.strip()
            if not self.name:
                raise ValueError("name is required")
        if self.description is not None:
            self.description = self.description.strip()
        return self


class BankItemAdd(BaseModel):
    model_config = ConfigDict(extra="forbid")
    questionIds: list[str] = Field(min_length=1)

    @field_validator("questionIds")
    @classmethod
    def normalize_question_ids(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value if item and item.strip()]
        if not normalized:
            raise ValueError("At least one question ID is required")
        return normalized


class BankMemberCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=64)
    role: BankRole

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        username = value.strip().lower()
        if not username:
            raise ValueError("username is required")
        return username


class BankMemberUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: BankRole


class BankForkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int | None = Field(default=None, gt=0)


class BankSubscriptionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(gt=0)


class BankPublicationEntity(BaseModel):
    id: int
    publicId: str
    bankId: int
    version: int
    state: dict[str, Any]
    createdBy: BankUserRef | None = None
    createdAt: datetime
    withdrawnAt: datetime | None = None


class BankVersionSummary(BaseModel):
    id: int
    publicId: str
    version: int
    createdBy: BankUserRef | None = None
    createdAt: datetime
    withdrawnAt: datetime | None = None
    isActive: bool = False


class BankSubscriptionEntity(BaseModel):
    bankId: int
    userId: int
    version: int | None = None
    createdAt: datetime
    updatedAt: datetime


class PublicBankSummary(BaseModel):
    publicId: str
    name: str
    description: str
    owner: BankUserRef | None = None
    version: int
    publishedAt: datetime
    itemCount: int = 0
    subscriberCount: int = 0


class PublicBankDetail(PublicBankSummary):
    state: dict[str, Any]
