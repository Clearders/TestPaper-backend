from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, TypeAdapter

from testpaper_backend.schemas.auth import UserEntity
from testpaper_backend.schemas.draft import DraftUserRef
from testpaper_backend.schemas.paper import PaperEntity
from testpaper_backend.schemas.question import QuestionEntity


class RealtimePing(BaseModel):
    event: Literal["ping"]


class DraftSubscribeEvent(BaseModel):
    event: Literal["draft.subscribe"]
    draftId: str


class DraftUnsubscribeEvent(BaseModel):
    event: Literal["draft.unsubscribe"]
    draftId: str


class DraftPresenceUpdateEvent(BaseModel):
    event: Literal["draft.presence.update"]
    draftId: str
    activity: Literal["viewing", "editing"]


RealtimeClientMessage = Annotated[
    RealtimePing | DraftSubscribeEvent | DraftUnsubscribeEvent | DraftPresenceUpdateEvent,
    Field(discriminator="event"),
]


class RealtimeServerEvent(BaseModel):
    """Common replay-safe envelope fields for every server-side realtime event."""

    eventId: UUID = Field(default_factory=uuid4)
    occurredAt: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AuthConnectedPayload(BaseModel):
    user: UserEntity
    serverTime: datetime


class AuthConnectedEvent(RealtimeServerEvent):
    event: Literal["auth.connected"]
    payload: AuthConnectedPayload


class ErrorPayload(BaseModel):
    message: str


class ErrorEvent(RealtimeServerEvent):
    event: Literal["error"]
    payload: ErrorPayload


class PongPayload(BaseModel):
    serverTime: datetime


class PongEvent(RealtimeServerEvent):
    event: Literal["pong"]
    payload: PongPayload


class QuestionChangedPayload(BaseModel):
    question: QuestionEntity
    actorId: int


class QuestionChangedEvent(RealtimeServerEvent):
    event: Literal["question.created", "question.updated"]
    payload: QuestionChangedPayload


class QuestionDeletedPayload(BaseModel):
    questionId: str
    actorId: int


class QuestionDeletedEvent(RealtimeServerEvent):
    event: Literal["question.deleted"]
    payload: QuestionDeletedPayload


class PaperChangedPayload(BaseModel):
    paper: PaperEntity
    actorId: int


class PaperChangedEvent(RealtimeServerEvent):
    event: Literal["paper.created", "paper.updated"]
    payload: PaperChangedPayload


class PaperQuestionsChangedPayload(PaperChangedPayload):
    paperId: str


class PaperQuestionsChangedEvent(RealtimeServerEvent):
    event: Literal["paper.questions.added", "paper.questions.reordered"]
    payload: PaperQuestionsChangedPayload


class PaperQuestionRemovedPayload(PaperQuestionsChangedPayload):
    questionId: str


class PaperQuestionRemovedEvent(RealtimeServerEvent):
    event: Literal["paper.question.removed"]
    payload: PaperQuestionRemovedPayload


class DraftChangedPayload(BaseModel):
    draftId: str
    revision: int
    reviewStatus: str
    actorId: int


class DraftChangedEvent(RealtimeServerEvent):
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


class DraftDeletedEvent(RealtimeServerEvent):
    event: Literal["draft.deleted"]
    payload: DraftDeletedPayload


class DraftPresenceMember(BaseModel):
    user: DraftUserRef
    activity: Literal["viewing", "editing"]
    lastSeenAt: datetime


class DraftPresenceSnapshotPayload(BaseModel):
    draftId: str
    members: list[DraftPresenceMember]


class DraftPresenceSnapshotEvent(RealtimeServerEvent):
    event: Literal["draft.presence.snapshot"]
    payload: DraftPresenceSnapshotPayload


class DraftCollaboratorsUpdatedEvent(RealtimeServerEvent):
    event: Literal["draft.collaborators.updated"]
    payload: DraftChangedPayload


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
    | DraftDeletedEvent
    | DraftPresenceSnapshotEvent
    | DraftCollaboratorsUpdatedEvent,
    Field(discriminator="event"),
]

CLIENT_MESSAGE_ADAPTER = TypeAdapter(RealtimeClientMessage)
SERVER_MESSAGE_ADAPTER = TypeAdapter(RealtimeServerMessage)


def validate_client_message(value: object) -> RealtimePing | DraftSubscribeEvent | DraftUnsubscribeEvent | DraftPresenceUpdateEvent:
    return CLIENT_MESSAGE_ADAPTER.validate_python(value)


def serialize_server_message(
    event: str,
    payload: dict[str, object],
    *,
    event_id: UUID | str | None = None,
    occurred_at: datetime | str | None = None,
) -> dict[str, object]:
    """Validate and serialize a server event, retaining relay envelope metadata."""

    message_data: dict[str, object] = {"event": event, "payload": payload}
    if event_id is not None:
        message_data["eventId"] = event_id
    if occurred_at is not None:
        message_data["occurredAt"] = occurred_at
    message = SERVER_MESSAGE_ADAPTER.validate_python(message_data)
    return message.model_dump(mode="json")
