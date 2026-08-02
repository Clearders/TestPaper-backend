from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter

from testpaper_backend.schemas.auth import UserEntity
from testpaper_backend.schemas.paper import PaperEntity
from testpaper_backend.schemas.question import QuestionEntity


class RealtimePing(BaseModel):
    event: Literal["ping"]


RealtimeClientMessage = Annotated[RealtimePing, Field(discriminator="event")]


class AuthConnectedPayload(BaseModel):
    user: UserEntity
    serverTime: datetime


class AuthConnectedEvent(BaseModel):
    event: Literal["auth.connected"]
    payload: AuthConnectedPayload


class ErrorPayload(BaseModel):
    message: str


class ErrorEvent(BaseModel):
    event: Literal["error"]
    payload: ErrorPayload


class PongPayload(BaseModel):
    serverTime: datetime


class PongEvent(BaseModel):
    event: Literal["pong"]
    payload: PongPayload


class QuestionChangedPayload(BaseModel):
    question: QuestionEntity
    actorId: int


class QuestionChangedEvent(BaseModel):
    event: Literal["question.created", "question.updated"]
    payload: QuestionChangedPayload


class QuestionDeletedPayload(BaseModel):
    questionId: str
    actorId: int


class QuestionDeletedEvent(BaseModel):
    event: Literal["question.deleted"]
    payload: QuestionDeletedPayload


class PaperChangedPayload(BaseModel):
    paper: PaperEntity
    actorId: int


class PaperChangedEvent(BaseModel):
    event: Literal["paper.created", "paper.updated"]
    payload: PaperChangedPayload


class PaperQuestionsChangedPayload(PaperChangedPayload):
    paperId: str


class PaperQuestionsChangedEvent(BaseModel):
    event: Literal["paper.questions.added", "paper.questions.reordered"]
    payload: PaperQuestionsChangedPayload


class PaperQuestionRemovedPayload(PaperQuestionsChangedPayload):
    questionId: str


class PaperQuestionRemovedEvent(BaseModel):
    event: Literal["paper.question.removed"]
    payload: PaperQuestionRemovedPayload


class DraftChangedPayload(BaseModel):
    draftId: str
    revision: int
    reviewStatus: str
    actorId: int


class DraftChangedEvent(BaseModel):
    event: Literal[
        "draft.updated",
        "draft.review.updated",
        "draft.comment.created",
        "draft.comment.updated",
    ]
    payload: DraftChangedPayload


class DraftDeletedPayload(BaseModel):
    draftId: str
    actorId: int


class DraftDeletedEvent(BaseModel):
    event: Literal["draft.deleted"]
    payload: DraftDeletedPayload


RealtimeServerMessage = Annotated[
    AuthConnectedEvent
    | ErrorEvent
    | PongEvent
    | QuestionChangedEvent
    | QuestionDeletedEvent
    | PaperChangedEvent
    | PaperQuestionsChangedEvent
    | PaperQuestionRemovedEvent
    | DraftChangedEvent
    | DraftDeletedEvent,
    Field(discriminator="event"),
]

CLIENT_MESSAGE_ADAPTER = TypeAdapter(RealtimeClientMessage)
SERVER_MESSAGE_ADAPTER = TypeAdapter(RealtimeServerMessage)


def validate_client_message(value: object) -> RealtimePing:
    return CLIENT_MESSAGE_ADAPTER.validate_python(value)


def serialize_server_message(event: str, payload: dict[str, object]) -> dict[str, object]:
    message = SERVER_MESSAGE_ADAPTER.validate_python({"event": event, "payload": payload})
    return message.model_dump(mode="json")
