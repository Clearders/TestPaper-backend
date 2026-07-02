from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DraftCollaboratorRole(StrEnum):
    viewer = "viewer"
    editor = "editor"


class DraftAccessRole(StrEnum):
    owner = "owner"
    admin = "admin"
    editor = "editor"
    viewer = "viewer"


class DraftCommentStatus(StrEnum):
    open = "open"
    resolved = "resolved"


class DraftReviewStatus(StrEnum):
    draft = "draft"
    in_review = "in_review"
    changes_requested = "changes_requested"
    approved = "approved"


class DraftUserRef(BaseModel):
    publicId: str
    username: str
    displayName: str


class PaperDraftCollaboratorEntity(BaseModel):
    user: DraftUserRef
    role: DraftCollaboratorRole
    createdAt: datetime
    updatedAt: datetime


class PaperDraftCommentEntity(BaseModel):
    id: int
    publicId: str
    questionPublicId: str | None = None
    message: str
    status: DraftCommentStatus
    author: DraftUserRef | None = None
    createdAt: datetime
    updatedAt: datetime


class PaperDraftSummary(BaseModel):
    id: int
    publicId: str
    name: str
    owner: DraftUserRef | None = None
    accessRole: DraftAccessRole
    reviewStatus: DraftReviewStatus
    revision: int
    collaboratorCount: int
    commentCount: int
    openCommentCount: int
    updatedBy: DraftUserRef | None = None
    createdAt: datetime
    updatedAt: datetime


class PaperDraftDetail(PaperDraftSummary):
    state: dict[str, Any]
    collaborators: list[PaperDraftCollaboratorEntity] = Field(default_factory=list)
    comments: list[PaperDraftCommentEntity] = Field(default_factory=list)


class PaperDraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    state: dict[str, Any]
    reviewStatus: DraftReviewStatus = DraftReviewStatus.draft

    @model_validator(mode="after")
    def normalize_create(self):
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("name is required")
        _validate_draft_state(self.state)
        return self


class PaperDraftUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    baseRevision: int = Field(gt=0)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    state: dict[str, Any] | None = None
    reviewStatus: DraftReviewStatus | None = None

    @model_validator(mode="after")
    def normalize_update(self):
        if self.name is None and self.state is None and self.reviewStatus is None:
            raise ValueError("At least one draft field must be updated")
        if self.name is not None:
            self.name = self.name.strip()
            if not self.name:
                raise ValueError("name is required")
        if self.state is not None:
            _validate_draft_state(self.state)
        return self


class PaperDraftCollaboratorCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=64)
    role: DraftCollaboratorRole

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        username = value.strip().lower()
        if not username:
            raise ValueError("username is required")
        return username


class PaperDraftCollaboratorUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: DraftCollaboratorRole


class PaperDraftCommentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    questionPublicId: str | None = Field(default=None, max_length=36)
    message: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def normalize_create(self):
        if self.questionPublicId is not None:
            self.questionPublicId = self.questionPublicId.strip() or None
        self.message = self.message.strip()
        if not self.message:
            raise ValueError("message is required")
        return self


class PaperDraftCommentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str | None = Field(default=None, min_length=1, max_length=1000)
    status: DraftCommentStatus | None = None

    @model_validator(mode="after")
    def normalize_update(self):
        if self.message is None and self.status is None:
            raise ValueError("At least one comment field must be updated")
        if self.message is not None:
            self.message = self.message.strip()
            if not self.message:
                raise ValueError("message is required")
        return self


def _validate_draft_state(state: dict[str, Any]) -> None:
    paper = state.get("paper")
    if not isinstance(paper, dict):
        raise ValueError("state.paper is required")
    for key in ("title", "subject"):
        if key in paper and not isinstance(paper[key], str):
            raise ValueError(f"state.paper.{key} must be a string")
    for key in ("duration", "totalMarks"):
        if key in paper and not isinstance(paper[key], int | float):
            raise ValueError(f"state.paper.{key} must be a number")
    questions = paper.get("questions", [])
    if not isinstance(questions, list):
        raise ValueError("state.paper.questions must be an array")
    for question in questions:
        if not isinstance(question, dict):
            raise ValueError("state.paper.questions must contain objects")
        if "publicId" in question and not isinstance(question["publicId"], str):
            raise ValueError("state.paper.questions[].publicId must be a string")
        if "text" in question and not isinstance(question["text"], str):
            raise ValueError("state.paper.questions[].text must be a string")
