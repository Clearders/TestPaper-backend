from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class QuestionType(StrEnum):
    choice = "choice"
    true_false = "true_false"
    blank = "blank"
    short_answer = "short_answer"
    essay = "essay"


class Difficulty(StrEnum):
    easy = "easy"
    medium = "medium"
    hard = "hard"


class SortOrder(StrEnum):
    asc = "asc"
    desc = "desc"


class PaperStatus(StrEnum):
    draft = "draft"
    published = "published"


class QuestionOrder(StrEnum):
    paper = "paper"
    categorized = "categorized"


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


class EssayBlankSpace(BaseModel):
    lines: int = Field(ge=1, le=20)
    lineHeight: int = Field(ge=20, le=48)


class QuestionImage(BaseModel):
    url: str = Field(min_length=1)
    caption: str | None = None


class QuestionBase(BaseModel):
    type: QuestionType
    subject: str = Field(min_length=1)
    difficulty: Difficulty
    tags: list[str] = Field(default_factory=list)
    text: str = Field(min_length=1)
    options: list[str] | None = None
    answer: str = Field(min_length=1)
    hasLatex: bool | None = None
    source: str | None = None
    essayBlankSpace: EssayBlankSpace | None = None
    images: list[QuestionImage] = Field(default_factory=list)
    scoreWeight: float = Field(default=1.0, gt=0, le=100)
    ownerId: int | None = None

    @model_validator(mode="after")
    def validate_question_type(self):
        if self.type in (QuestionType.choice, QuestionType.true_false):
            if not self.options:
                raise ValueError(f"{self.type.value} questions require options")
        else:
            self.options = None

        if self.type == QuestionType.essay:
            if self.essayBlankSpace is None:
                self.essayBlankSpace = EssayBlankSpace(lines=6, lineHeight=28)
        else:
            self.essayBlankSpace = None

        self.tags = [tag.strip() for tag in self.tags if tag and tag.strip()]
        if self.options is not None:
            self.options = [option.strip() for option in self.options if option and option.strip()]
        if self.source is not None:
            self.source = self.source.strip() or None
        return self


class QuestionCreate(QuestionBase):
    pass


class QuestionUpdate(BaseModel):
    type: QuestionType | None = None
    subject: str | None = Field(default=None, min_length=1)
    difficulty: Difficulty | None = None
    tags: list[str] | None = None
    text: str | None = Field(default=None, min_length=1)
    options: list[str] | None = None
    answer: str | None = Field(default=None, min_length=1)
    hasLatex: bool | None = None
    source: str | None = None
    essayBlankSpace: EssayBlankSpace | None = None
    images: list[QuestionImage] | None = None
    scoreWeight: float | None = Field(default=None, gt=0, le=100)
    ownerId: int | None = None


class QuestionEntity(QuestionBase):
    id: int
    publicId: str
    createdAt: datetime
    updatedAt: datetime


class QuestionRef(BaseModel):
    questionId: int = Field(gt=0)
    orderNo: int = Field(gt=0)
    marks: int | None = Field(default=None, gt=0)


class PaperQuestion(QuestionRef):
    pass


class PaperBase(BaseModel):
    title: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    duration: int = Field(gt=0)
    totalMarks: int = Field(gt=0)

    @model_validator(mode="after")
    def normalize_paper_base(self):
        self.title = self.title.strip()
        self.subject = self.subject.strip()
        if not self.title:
            raise ValueError("title is required")
        if not self.subject:
            raise ValueError("subject is required")
        return self


class PaperCreate(PaperBase):
    questions: list[QuestionRef] = Field(default_factory=list)


class PaperUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1)
    subject: str | None = Field(default=None, min_length=1)
    duration: int | None = Field(default=None, gt=0)
    totalMarks: int | None = Field(default=None, gt=0)
    status: PaperStatus | None = None


class PaperEntity(PaperBase):
    id: int
    publicId: str
    questions: list[PaperQuestion]
    status: PaperStatus = PaperStatus.draft
    createdAt: datetime
    updatedAt: datetime


class GenerationTypeTarget(BaseModel):
    questionType: QuestionType
    count: int = Field(gt=0)


class PaperGenerateRequest(PaperBase):
    difficultyCoefficient: float = Field(ge=0, le=1)
    questionTypes: list[GenerationTypeTarget] = Field(min_length=1)
    ownQuestionsOnly: bool = False
    requiredTags: list[str] = Field(default_factory=list)
    preferredTags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalize_generation_request(self):
        self.difficultyCoefficient = round(self.difficultyCoefficient, 2)
        self.requiredTags = [tag.strip().lower() for tag in self.requiredTags if tag and tag.strip()]
        self.preferredTags = [tag.strip().lower() for tag in self.preferredTags if tag and tag.strip()]
        return self


class QuestionOrderItem(BaseModel):
    questionId: int = Field(gt=0)
    orderNo: int = Field(gt=0)


class QuestionOrderUpdate(BaseModel):
    orders: list[QuestionOrderItem]


class ExportPreviewRequest(BaseModel):
    format: Literal["json"] = "json"
    includeAnswer: bool = True
    questionOrder: QuestionOrder = QuestionOrder.paper


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    displayName: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=6, max_length=128)

    @model_validator(mode="after")
    def normalize_register_request(self):
        self.username = self.username.strip().lower()
        self.displayName = self.displayName.strip()
        return self


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    displayName: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=6, max_length=128)
    role: UserRole = UserRole.viewer
    isActive: bool = True

    @model_validator(mode="after")
    def normalize_user_create(self):
        self.username = self.username.strip().lower()
        self.displayName = self.displayName.strip()
        return self


class UserUpdate(BaseModel):
    displayName: str | None = Field(default=None, min_length=1, max_length=120)
    password: str | None = Field(default=None, min_length=6, max_length=128)
    role: UserRole | None = None
    isActive: bool | None = None

    @model_validator(mode="after")
    def normalize_user_update(self):
        if self.displayName is not None:
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
    createdAt: datetime
    updatedAt: datetime


class AuthSession(BaseModel):
    expiresAt: datetime
    user: UserEntity


class ImageUploadPayload(BaseModel):
    filename: str = Field(min_length=1)
    data: str = Field(min_length=1)
    mimeType: str = Field(default="image/png")


class ImageUploadResponse(BaseModel):
    url: str
    filename: str
    mimeType: str
