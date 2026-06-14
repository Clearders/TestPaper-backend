from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class CorrectionCategory(StrEnum):
    wrong_answer = "wrong_answer"
    unclear = "unclear"
    typo = "typo"
    other = "other"


class CorrectionStatus(StrEnum):
    open = "open"
    accepted = "accepted"
    rejected = "rejected"


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
    answer: str | list[str] = Field(default="")
    hasLatex: bool | None = None
    source: str | None = None
    essayBlankSpace: EssayBlankSpace | None = None
    images: list[QuestionImage] = Field(default_factory=list)
    scoreWeight: float = Field(default=1.0, gt=0, le=100)
    ownerId: int | None = None

    @model_validator(mode="after")
    def validate_question_type(self):
        option_types = (QuestionType.single_choice, QuestionType.multiple_choice, QuestionType.true_false)
        self.text = self.text.strip()
        self.tags = list(dict.fromkeys(tag.strip().lower() for tag in self.tags if tag and tag.strip()))
        self.subjects = list(dict.fromkeys(subject.strip() for subject in self.subjects if subject and subject.strip()))
        if self.options is not None:
            self.options = [option.strip() for option in self.options if option and option.strip()]
        if self.source is not None:
            self.source = self.source.strip() or None

        if not self.text:
            raise ValueError("Question text is required")
        if not self.subjects:
            raise ValueError("At least one non-empty subject is required")
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
        return self

    @model_validator(mode="after")
    def validate_question_answer(self):
        if self.type == QuestionType.multiple_choice:
            if self.answer and not isinstance(self.answer, list):
                raise ValueError("multiple_choice questions require answer to be a list")
            if isinstance(self.answer, list):
                self.answer = list(dict.fromkeys(item.strip() for item in self.answer if item and item.strip()))
        elif self.answer and not isinstance(self.answer, str):
            raise ValueError(f"{self.type.value} questions require answer to be a string")
        elif isinstance(self.answer, str):
            self.answer = self.answer.strip()
        return self


class QuestionCreate(QuestionBase):
    @model_validator(mode="after")
    def validate_answer_not_empty(self):
        if not self.answer:
            raise ValueError("answer is required")
        return self


class QuestionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: QuestionType | None = None
    subjects: list[str] | None = None
    difficulty: Difficulty | None = None
    tags: list[str] | None = None
    text: str | None = Field(default=None, min_length=1)
    options: list[str] | None = None
    answer: str | list[str] | None = Field(default=None)
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


class QuestionCorrectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: CorrectionCategory
    message: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def normalize_message(self):
        self.message = self.message.strip()
        if not self.message:
            raise ValueError("Correction message is required")
        return self


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
