from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from testpaper_backend.schemas.question import Difficulty, EssayBlankSpace, QuestionEntity, QuestionImage, QuestionRef, QuestionType


class PaperStatus(StrEnum):
    draft = "draft"
    published = "published"


class QuestionOrder(StrEnum):
    paper = "paper"
    categorized = "categorized"


class LayoutDensity(StrEnum):
    auto = "auto"
    normal = "normal"
    compact = "compact"
    dense = "dense"


class PaperBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    duration: int = Field(gt=0)
    totalMarks: int = Field(gt=0)
    ownerId: int | None = None

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

    @model_validator(mode="after")
    def normalize_paper_update(self):
        for field_name in ("title", "subject", "duration", "totalMarks", "status"):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null")
        if self.title is not None:
            self.title = self.title.strip()
            if not self.title:
                raise ValueError("title is required")
        if self.subject is not None:
            self.subject = self.subject.strip()
            if not self.subject:
                raise ValueError("subject is required")
        return self


class PaperEntity(PaperBase):
    id: int
    publicId: str
    questions: list[QuestionRef]
    status: PaperStatus = PaperStatus.draft
    createdAt: datetime
    updatedAt: datetime


class PaperQuestionEntity(QuestionEntity):
    questionPublicId: str
    orderNo: int
    marks: int | None = None


class PaperExpandedEntity(PaperBase):
    id: int
    publicId: str
    questions: list[PaperQuestionEntity]
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
        self.requiredTags = list(dict.fromkeys(self.requiredTags))
        self.preferredTags = list(dict.fromkeys(self.preferredTags))
        self.subjects = list(dict.fromkeys(s.strip() for s in self.subjects if s and s.strip()))
        self.subject = ", ".join(self.subjects) if self.subjects else "-"
        if not self.title:
            raise ValueError("title is required")
        if not self.subjects:
            raise ValueError("At least one non-empty subject is required")
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
    layoutDensity: LayoutDensity = LayoutDensity.auto


class PaperDraftQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    questionPublicId: str = Field(min_length=1)
    orderNo: int = Field(gt=0)
    marks: int | None = Field(default=None, gt=0)
    type: QuestionType
    subjects: list[str] = Field(default_factory=list)
    difficulty: Difficulty = Difficulty.medium
    tags: list[str] = Field(default_factory=list)
    text: str = Field(min_length=1)
    options: list[str] | None = None
    answer: str | list[str] = ""
    hasLatex: bool = False
    source: str | None = None
    essayBlankSpace: EssayBlankSpace | None = None
    images: list[QuestionImage] = Field(default_factory=list)
    scoreWeight: float = Field(default=1, gt=0, le=100)

    @model_validator(mode="after")
    def normalize_draft_question(self):
        self.questionPublicId = self.questionPublicId.strip()
        self.text = self.text.strip()
        self.subjects = list(dict.fromkeys(subject.strip() for subject in self.subjects if subject and subject.strip()))
        self.tags = list(dict.fromkeys(tag.strip().lower() for tag in self.tags if tag and tag.strip()))
        if self.options is not None:
            self.options = [option.strip() for option in self.options if option and option.strip()]
        if isinstance(self.answer, str):
            self.answer = self.answer.strip()
        else:
            self.answer = list(dict.fromkeys(item.strip() for item in self.answer if item and item.strip()))
        if self.source is not None:
            self.source = self.source.strip() or None
        if not self.questionPublicId:
            raise ValueError("questionPublicId is required")
        if not self.text:
            raise ValueError("text is required")
        return self


class PaperDraftDownloadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    duration: int = Field(gt=0)
    totalMarks: int = Field(gt=0)
    questions: list[PaperDraftQuestion] = Field(min_length=1)
    includeAnswer: bool = True
    questionOrder: QuestionOrder = QuestionOrder.paper
    layoutDensity: LayoutDensity = LayoutDensity.auto

    @model_validator(mode="after")
    def normalize_draft_download(self):
        self.title = self.title.strip()
        self.subject = self.subject.strip()
        if not self.title:
            raise ValueError("title is required")
        if not self.subject:
            raise ValueError("subject is required")
        return self
