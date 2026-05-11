from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class QuestionType(str, Enum):
    choice = "choice"
    true_false = "true_false"
    blank = "blank"
    short_answer = "short_answer"
    essay = "essay"


class Difficulty(str, Enum):
    easy = "easy"
    medium = "medium"
    hard = "hard"


class SortOrder(str, Enum):
    asc = "asc"
    desc = "desc"


class PaperStatus(str, Enum):
    draft = "draft"
    published = "published"


class QuestionOrder(str, Enum):
    paper = "paper"
    categorized = "categorized"


class GenerationAllocationMode(str, Enum):
    question_count = "question_count"
    total_score = "total_score"


class UserRole(str, Enum):
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
        "questions:delete",
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
    questions: list[PaperQuestion]
    status: PaperStatus = PaperStatus.draft
    createdAt: datetime
    updatedAt: datetime


class GeneticAlgorithmOptions(BaseModel):
    populationSize: int = Field(default=80, ge=20, le=500)
    generations: int = Field(default=120, ge=10, le=1000)
    crossoverRate: float = Field(default=0.85, ge=0, le=1)
    mutationRate: float = Field(default=0.08, ge=0, le=1)
    elitismCount: int = Field(default=4, ge=1, le=50)
    tournamentSize: int = Field(default=3, ge=2, le=20)
    randomSeed: int | None = None


class PaperGenerateRequest(PaperBase):
    allocationMode: GenerationAllocationMode = GenerationAllocationMode.question_count
    questionCount: int | None = Field(default=None, ge=1, le=100)
    difficultyTargets: dict[Difficulty, int] = Field(default_factory=dict)
    typeTargets: dict[QuestionType, int] = Field(default_factory=dict)
    requiredTags: list[str] = Field(default_factory=list)
    optionalTags: list[str] = Field(default_factory=list)
    subjectStrict: bool = True
    algorithm: GeneticAlgorithmOptions = Field(default_factory=GeneticAlgorithmOptions)

    @model_validator(mode="after")
    def normalize_generation_request(self):
        if self.allocationMode == GenerationAllocationMode.question_count and self.questionCount is None:
            self.questionCount = 10
        if self.questionCount is not None and self.totalMarks < self.questionCount:
            raise ValueError("totalMarks must be greater than or equal to questionCount")
        self.requiredTags = [tag.strip() for tag in self.requiredTags if tag and tag.strip()]
        self.optionalTags = [tag.strip() for tag in self.optionalTags if tag and tag.strip()]
        self.difficultyTargets = {key: value for key, value in self.difficultyTargets.items() if value > 0}
        self.typeTargets = {key: value for key, value in self.typeTargets.items() if value > 0}
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
