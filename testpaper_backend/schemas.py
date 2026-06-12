from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

T = TypeVar("T")


class QuestionType(StrEnum):
    single_choice = "single_choice"
    multiple_choice = "multiple_choice"
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
    model_config = ConfigDict(extra="forbid")
    type: QuestionType
    subjects: list[str] = Field(min_length=1)
    difficulty: Difficulty
    tags: list[str] = Field(default_factory=list)
    text: str = Field(min_length=1)
    options: list[str] | None = None
    answer: str | list[str] = Field(min_length=1)
    hasLatex: bool | None = None
    source: str | None = None
    essayBlankSpace: EssayBlankSpace | None = None
    images: list[QuestionImage] = Field(default_factory=list)
    scoreWeight: float = Field(default=1.0, gt=0, le=100)
    ownerId: int | None = None

    @model_validator(mode="after")
    def validate_question_type(self):
        option_types = (QuestionType.single_choice, QuestionType.multiple_choice, QuestionType.true_false)
        if self.type in option_types:
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
        self.subjects = [s.strip() for s in self.subjects if s and s.strip()]
        if self.options is not None:
            self.options = [option.strip() for option in self.options if option and option.strip()]
        if self.source is not None:
            self.source = self.source.strip() or None
        return self

    @model_validator(mode="after")
    def validate_question_answer(self):
        if self.type == QuestionType.multiple_choice:
            if not isinstance(self.answer, list):
                raise ValueError("multiple_choice questions require answer to be a list")
            if len(self.answer) < 1:
                raise ValueError("multiple_choice questions require at least one answer")
        else:
            if not isinstance(self.answer, str):
                raise ValueError(f"{self.type.value} questions require answer to be a string")
        return self


class QuestionCreate(QuestionBase):
    pass


class QuestionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: QuestionType | None = None
    subjects: list[str] | None = None
    difficulty: Difficulty | None = None
    tags: list[str] | None = None
    text: str | None = Field(default=None, min_length=1)
    options: list[str] | None = None
    answer: str | list[str] | None = Field(default=None, min_length=1)
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
    questionPublicId: str = Field(min_length=1)
    orderNo: int = Field(gt=0)
    marks: int | None = Field(default=None, gt=0)


class PaperBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
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
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1)
    subject: str | None = Field(default=None, min_length=1)
    duration: int | None = Field(default=None, gt=0)
    totalMarks: int | None = Field(default=None, gt=0)
    status: PaperStatus | None = None


class PaperEntity(PaperBase):
    id: int
    publicId: str
    questions: list[QuestionRef]
    status: PaperStatus = PaperStatus.draft
    createdAt: datetime
    updatedAt: datetime


class GenerationTypeTarget(BaseModel):
    questionType: QuestionType
    count: int = Field(gt=0)


class PaperGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1)
    duration: int = Field(gt=0)
    totalMarks: int = Field(gt=0)
    difficultyCoefficient: float = Field(ge=0, le=1)
    questionTypes: list[GenerationTypeTarget] = Field(min_length=1)
    ownQuestionsOnly: bool = False
    requiredTags: list[str] = Field(default_factory=list)
    preferredTags: list[str] = Field(default_factory=list)
    subjects: list[str] = Field(default_factory=list, min_length=1)
    subject: str = Field(default="-", min_length=1)

    @model_validator(mode="after")
    def normalize_generation_request(self):
        self.title = self.title.strip()
        self.difficultyCoefficient = round(self.difficultyCoefficient, 2)
        self.requiredTags = [tag.strip().lower() for tag in self.requiredTags if tag and tag.strip()]
        self.preferredTags = [tag.strip().lower() for tag in self.preferredTags if tag and tag.strip()]
        self.subjects = [s.strip() for s in self.subjects if s.strip()]
        self.subject = ", ".join(self.subjects) if self.subjects else "-"
        if not self.title:
            raise ValueError("title is required")
        return self


class QuestionOrderItem(BaseModel):
    questionPublicId: str = Field(min_length=1)
    orderNo: int = Field(gt=0)


class QuestionOrderUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    orders: list[QuestionOrderItem]


class ExportPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    includeAnswer: bool = True
    questionOrder: QuestionOrder = QuestionOrder.paper


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=64)
    displayName: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=128)
    @model_validator(mode="after")
    def normalize_register_request(self):
        self.username = self.username.strip().lower()
        self.displayName = self.displayName.strip()
        return self


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=64)
    displayName: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=128)
    role: UserRole = UserRole.viewer
    isActive: bool = True

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


class ImageUploadPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(min_length=1)
    data: str = Field(min_length=1)
    mimeType: str = Field(default="image/png")


class ImageUploadResponse(BaseModel):
    url: str
    filename: str
    mimeType: str


class CorrectionCategory(StrEnum):
    wrong_answer = "wrong_answer"
    unclear = "unclear"
    typo = "typo"
    other = "other"


class CorrectionStatus(StrEnum):
    open = "open"
    accepted = "accepted"
    rejected = "rejected"


class QuestionCorrectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: CorrectionCategory
    message: str = Field(min_length=1, max_length=1000)


class QuestionCorrectionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: CorrectionStatus


class QuestionCorrectionEntity(BaseModel):
    id: int
    questionId: int
    userId: int | None
    category: CorrectionCategory
    message: str
    status: CorrectionStatus
    createdAt: datetime
    updatedAt: datetime


class QuestionRevisionEntity(BaseModel):
    id: int
    questionId: int
    userId: int | None
    patch: dict[str, Any]
    changeSummary: str
    createdAt: datetime


class MetaInfo(BaseModel):
    requestId: str


class Envelope(BaseModel, Generic[T]):
    success: bool = True
    data: T
    meta: MetaInfo


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: Any | None = None


class ErrorEnvelope(BaseModel):
    success: bool = False
    error: ErrorDetail
    meta: MetaInfo


class PaginationInfo(BaseModel):
    page: int
    pageSize: int
    total: int
    totalPages: int


class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    pagination: PaginationInfo
