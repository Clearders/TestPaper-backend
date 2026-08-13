from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from testpaper_backend.config import get_database_url
from testpaper_backend.schemas import PaperStatus, TokenType, UserRole


class Base(DeclarativeBase):
    pass


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column("publicId", String(36), nullable=False, unique=True, index=True, default=lambda: str(uuid4()))
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    display_name: Mapped[str] = mapped_column("displayName", String(120), nullable=False)
    password_hash: Mapped[str] = mapped_column("passwordHash", String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default=UserRole.viewer.value)
    is_active: Mapped[bool] = mapped_column("isActive", Boolean, nullable=False, default=True)
    avatar_url: Mapped[str | None] = mapped_column("avatarUrl", String(512), nullable=True)
    last_username_changed_at: Mapped[datetime | None] = mapped_column("lastUsernameChangedAt", DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    tokens: Mapped[list[AuthTokenRow]] = relationship(back_populates="user", cascade="all, delete-orphan")


class AuthTokenRow(Base):
    __tablename__ = "auth_tokens"

    token: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_type: Mapped[str] = mapped_column("tokenType", String(16), nullable=False, default=TokenType.session.value)
    device_id: Mapped[str | None] = mapped_column("deviceId", String(128), nullable=True, index=True)
    device_name: Mapped[str | None] = mapped_column("deviceName", String(120), nullable=True)
    ip_address: Mapped[str | None] = mapped_column("ipAddress", String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column("userAgent", String(512), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column("lastSeenAt", DateTime(timezone=True), nullable=True)
    refresh_token_id: Mapped[str | None] = mapped_column("refreshTokenId", String(128), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    user: Mapped[UserRow] = relationship(back_populates="tokens")


class AuthAuditLogRow(Base):
    __tablename__ = "auth_audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column("userId", ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    device_id: Mapped[str | None] = mapped_column("deviceId", String(128), nullable=True)
    event: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    ip_address: Mapped[str] = mapped_column("ipAddress", String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class QuestionRow(Base):
    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column("publicId", String(36), nullable=False, unique=True, index=True, default=lambda: str(uuid4()))
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    subjects: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    difficulty: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    text: Mapped[str] = mapped_column(String, nullable=False)
    options: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    answer: Mapped[Any] = mapped_column(JSONB, nullable=False)
    has_latex: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source: Mapped[str | None] = mapped_column(String, nullable=True)
    essay_blank_space: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    images: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    score_weight: Mapped[float] = mapped_column("scoreWeight", Float, nullable=False, default=1.0)
    owner_id: Mapped[int | None] = mapped_column("ownerId", ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PaperRow(Base):
    __tablename__ = "papers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column("publicId", String(36), nullable=False, unique=True, index=True, default=lambda: str(uuid4()))
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    duration: Mapped[int] = mapped_column(Integer, nullable=False)
    total_marks: Mapped[int] = mapped_column("totalMarks", Integer, nullable=False)
    owner_id: Mapped[int | None] = mapped_column("ownerId", ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=PaperStatus.draft.value)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    questions: Mapped[list[PaperQuestionRow]] = relationship(
        back_populates="paper",
        cascade="all, delete-orphan",
        order_by="PaperQuestionRow.order_no",
    )


class PaperQuestionRow(Base):
    __tablename__ = "paper_questions"
    __table_args__ = (UniqueConstraint("paper_id", "orderNo", name="uq_paper_question_order"),)

    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"), primary_key=True)
    order_no: Mapped[int] = mapped_column("orderNo", Integer, nullable=False)
    marks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    paper: Mapped[PaperRow] = relationship(back_populates="questions")


class PaperDraftRow(Base):
    __tablename__ = "paper_drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column("publicId", String(36), nullable=False, unique=True, index=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    owner_id: Mapped[int | None] = mapped_column("ownerId", ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    review_status: Mapped[str] = mapped_column("reviewStatus", String(32), nullable=False, default="draft", index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by: Mapped[int | None] = mapped_column("updatedBy", ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    owner: Mapped[UserRow | None] = relationship(foreign_keys=[owner_id])
    updated_by_user: Mapped[UserRow | None] = relationship(foreign_keys=[updated_by])
    collaborators: Mapped[list[PaperDraftCollaboratorRow]] = relationship(
        back_populates="draft",
        cascade="all, delete-orphan",
    )
    comments: Mapped[list[PaperDraftCommentRow]] = relationship(
        back_populates="draft",
        cascade="all, delete-orphan",
        order_by="PaperDraftCommentRow.created_at",
    )


class PaperDraftCollaboratorRow(Base):
    __tablename__ = "paper_draft_collaborators"

    draft_id: Mapped[int] = mapped_column("draftId", ForeignKey("paper_drafts.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column("userId", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    draft: Mapped[PaperDraftRow] = relationship(back_populates="collaborators")
    user: Mapped[UserRow] = relationship()


class PaperDraftCommentRow(Base):
    __tablename__ = "paper_draft_comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column("publicId", String(36), nullable=False, unique=True, index=True, default=lambda: str(uuid4()))
    draft_id: Mapped[int] = mapped_column("draftId", ForeignKey("paper_drafts.id", ondelete="CASCADE"), nullable=False, index=True)
    question_public_id: Mapped[str | None] = mapped_column("questionPublicId", String(36), nullable=True, index=True)
    message: Mapped[str] = mapped_column(String(1000), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open", index=True)
    author_id: Mapped[int | None] = mapped_column("authorId", ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    draft: Mapped[PaperDraftRow] = relationship(back_populates="comments")
    author: Mapped[UserRow | None] = relationship()


class QuestionRevisionRow(Base):
    __tablename__ = "question_revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    patch: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    change_summary: Mapped[str] = mapped_column("changeSummary", String(500), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class QuestionCorrectionRow(Base):
    __tablename__ = "question_corrections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    message: Mapped[str] = mapped_column(String(1000), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class QuestionBankRow(Base):
    __tablename__ = "question_banks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column("publicId", String(36), nullable=False, unique=True, index=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    owner_id: Mapped[int | None] = mapped_column("ownerId", ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    visibility: Mapped[str] = mapped_column(String(16), nullable=False, default="private", index=True)
    latest_version: Mapped[int] = mapped_column("latestVersion", Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    owner: Mapped[UserRow | None] = relationship(foreign_keys=[owner_id])
    items: Mapped[list[QuestionBankItemRow]] = relationship(
        back_populates="bank",
        cascade="all, delete-orphan",
    )
    members: Mapped[list[QuestionBankMemberRow]] = relationship(
        back_populates="bank",
        cascade="all, delete-orphan",
    )
    publications: Mapped[list[BankPublicationRow]] = relationship(
        back_populates="bank",
        cascade="all, delete-orphan",
        order_by="BankPublicationRow.version",
    )
    subscriptions: Mapped[list[BankSubscriptionRow]] = relationship(
        back_populates="bank",
        cascade="all, delete-orphan",
    )


class QuestionBankItemRow(Base):
    __tablename__ = "question_bank_items"

    bank_id: Mapped[int] = mapped_column("bankId", ForeignKey("question_banks.id", ondelete="CASCADE"), primary_key=True, index=True)
    question_id: Mapped[int] = mapped_column("questionId", ForeignKey("questions.id", ondelete="CASCADE"), primary_key=True)
    added_by: Mapped[int | None] = mapped_column("addedBy", ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    bank: Mapped[QuestionBankRow] = relationship(back_populates="items")
    question: Mapped[QuestionRow] = relationship()


class QuestionBankMemberRow(Base):
    __tablename__ = "question_bank_members"

    bank_id: Mapped[int] = mapped_column("bankId", ForeignKey("question_banks.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column("userId", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    bank: Mapped[QuestionBankRow] = relationship(back_populates="members")
    user: Mapped[UserRow] = relationship()


class BankPublicationRow(Base):
    __tablename__ = "bank_publications"
    __table_args__ = (UniqueConstraint("bankId", "version", name="uq_bank_publications_bank_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column("publicId", String(36), nullable=False, unique=True, index=True, default=lambda: str(uuid4()))
    bank_id: Mapped[int] = mapped_column("bankId", ForeignKey("question_banks.id", ondelete="CASCADE"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[int | None] = mapped_column("createdBy", ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    withdrawn_at: Mapped[datetime | None] = mapped_column("withdrawnAt", DateTime(timezone=True), nullable=True, index=True)
    bank: Mapped[QuestionBankRow] = relationship(back_populates="publications")
    created_by_user: Mapped[UserRow | None] = relationship(foreign_keys=[created_by])


class BankSubscriptionRow(Base):
    __tablename__ = "bank_subscriptions"

    bank_id: Mapped[int] = mapped_column("bankId", ForeignKey("question_banks.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column("userId", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    publication_id: Mapped[int | None] = mapped_column(
        "publicationId",
        ForeignKey("bank_publications.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    bank: Mapped[QuestionBankRow] = relationship(back_populates="subscriptions")
    user: Mapped[UserRow] = relationship()
    publication: Mapped[BankPublicationRow | None] = relationship()


class SyncEntityRow(Base):
    __tablename__ = "sync_entities"
    __table_args__ = (
        UniqueConstraint("id", "ownerId", name="uq_sync_entities_id_owner"),
        UniqueConstraint("ownerId", "entityType", "publicId", name="uq_sync_entities_owner_type_public_id"),
        CheckConstraint(
            "\"entityType\" IN ('question', 'paper', 'draft', 'attachment', 'comment', 'favorite', 'setting')",
            name="ck_sync_entities_entity_type",
        ),
        CheckConstraint('"schemaVersion" >= 1', name="ck_sync_entities_schema_version"),
        CheckConstraint('"version" >= 1', name="ck_sync_entities_version"),
        CheckConstraint('length("contentHash") = 64', name="ck_sync_entities_content_hash"),
        CheckConstraint(
            '("tombstone" AND "deletedAt" IS NOT NULL) OR (NOT "tombstone" AND "deletedAt" IS NULL)',
            name="ck_sync_entities_tombstone_deleted_at",
        ),
        Index("ix_sync_entities_owner_scope_updated", "ownerId", "scope", "updatedAt", "id"),
        Index("ix_sync_entities_owner_type_tombstone", "ownerId", "entityType", "tombstone"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column("ownerId", ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    entity_type: Mapped[str] = mapped_column("entityType", String(32), nullable=False)
    public_id: Mapped[str] = mapped_column("publicId", String(36), nullable=False)
    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column("schemaVersion", Integer, nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_hash: Mapped[str] = mapped_column("contentHash", String(64), nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    tombstone: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column("createdAt", DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column("updatedAt", DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column("deletedAt", DateTime(timezone=True), nullable=True)
    versions: Mapped[list[SyncEntityVersionRow]] = relationship(
        back_populates="entity",
        cascade="save-update, merge",
        order_by="SyncEntityVersionRow.version",
    )


class SyncEntityVersionRow(Base):
    __tablename__ = "sync_entity_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["entityId", "ownerId"],
            ["sync_entities.id", "sync_entities.ownerId"],
            ondelete="CASCADE",
            name="fk_sync_entity_versions_entity_owner",
        ),
        UniqueConstraint("entityId", "version", name="uq_sync_entity_versions_entity_version"),
        UniqueConstraint("ownerId", "operationId", name="uq_sync_entity_versions_owner_operation"),
        CheckConstraint('"version" >= 1', name="ck_sync_entity_versions_version"),
        CheckConstraint('"schemaVersion" >= 1', name="ck_sync_entity_versions_schema_version"),
        CheckConstraint('length("contentHash") = 64', name="ck_sync_entity_versions_content_hash"),
        CheckConstraint(
            "\"mutationKind\" IN ('create', 'update', 'delete', 'restore')",
            name="ck_sync_entity_versions_mutation_kind",
        ),
        Index("ix_sync_entity_versions_entity_created", "entityId", "createdAt"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    entity_id: Mapped[int] = mapped_column("entityId", BigInteger, nullable=False)
    owner_id: Mapped[int] = mapped_column("ownerId", Integer, nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    schema_version: Mapped[int] = mapped_column("schemaVersion", Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column("contentHash", String(64), nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    tombstone: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    mutation_kind: Mapped[str] = mapped_column("mutationKind", String(16), nullable=False)
    operation_id: Mapped[str] = mapped_column("operationId", String(36), nullable=False)
    base_version: Mapped[int | None] = mapped_column("baseVersion", BigInteger, nullable=True)
    base_hash: Mapped[str | None] = mapped_column("baseHash", String(64), nullable=True)
    device_id: Mapped[str] = mapped_column("deviceId", String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column("createdAt", DateTime(timezone=True), nullable=False)
    entity: Mapped[SyncEntityRow] = relationship(back_populates="versions")
    change: Mapped[SyncChangeLogRow | None] = relationship(back_populates="entity_version", uselist=False)


class SyncChangeLogRow(Base):
    __tablename__ = "sync_change_log"
    __table_args__ = (
        UniqueConstraint("entityVersionId", name="uq_sync_change_log_entity_version"),
        CheckConstraint('"version" >= 1', name="ck_sync_change_log_version"),
        CheckConstraint('length("contentHash") = 64', name="ck_sync_change_log_content_hash"),
        Index("ix_sync_change_log_pull", "ownerId", "scope", "sequence"),
        Index("ix_sync_change_log_compaction", "ownerId", "createdAt", "sequence"),
    )

    sequence: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    entity_version_id: Mapped[int] = mapped_column(
        "entityVersionId",
        ForeignKey("sync_entity_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    owner_id: Mapped[int] = mapped_column("ownerId", ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column("entityType", String(32), nullable=False)
    public_id: Mapped[str] = mapped_column("publicId", String(36), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_hash: Mapped[str] = mapped_column("contentHash", String(64), nullable=False)
    mutation_kind: Mapped[str] = mapped_column("mutationKind", String(16), nullable=False)
    tombstone: Mapped[bool] = mapped_column(Boolean, nullable=False)
    operation_id: Mapped[str] = mapped_column("operationId", String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column("createdAt", DateTime(timezone=True), nullable=False)
    entity_version: Mapped[SyncEntityVersionRow] = relationship(back_populates="change")


class SyncStreamRow(Base):
    __tablename__ = "sync_streams"
    __table_args__ = (
        CheckConstraint('"retainedFromSequence" >= 0', name="ck_sync_streams_retained_sequence"),
        CheckConstraint('"snapshotVersion" >= 0', name="ck_sync_streams_snapshot_version"),
    )

    owner_id: Mapped[int] = mapped_column("ownerId", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    scope: Mapped[str] = mapped_column(String(64), primary_key=True)
    epoch: Mapped[str] = mapped_column(String(36), nullable=False)
    retained_from_sequence: Mapped[int] = mapped_column("retainedFromSequence", BigInteger, nullable=False, default=0)
    snapshot_version: Mapped[int] = mapped_column("snapshotVersion", BigInteger, nullable=False, default=0)
    compacted_at: Mapped[datetime | None] = mapped_column("compactedAt", DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column("createdAt", DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column("updatedAt", DateTime(timezone=True), nullable=False)


class SyncDeviceCursorRow(Base):
    __tablename__ = "sync_device_cursors"
    __table_args__ = (
        ForeignKeyConstraint(
            ["ownerId", "scope"],
            ["sync_streams.ownerId", "sync_streams.scope"],
            ondelete="CASCADE",
            name="fk_sync_device_cursors_stream",
        ),
        CheckConstraint('"cursorSequence" >= 0', name="ck_sync_device_cursors_sequence"),
        CheckConstraint('"protocolVersion" >= 1', name="ck_sync_device_cursors_protocol_version"),
        Index("ix_sync_device_cursors_expiry", "expiresAt", "revokedAt"),
        Index("ix_sync_device_cursors_owner_seen", "ownerId", "lastSeenAt"),
    )

    owner_id: Mapped[int] = mapped_column("ownerId", Integer, primary_key=True)
    device_id: Mapped[str] = mapped_column("deviceId", String(128), primary_key=True)
    scope: Mapped[str] = mapped_column(String(64), primary_key=True)
    stream_epoch: Mapped[str] = mapped_column("streamEpoch", String(36), nullable=False)
    cursor_sequence: Mapped[int] = mapped_column("cursorSequence", BigInteger, nullable=False, default=0)
    protocol_version: Mapped[int] = mapped_column("protocolVersion", Integer, nullable=False)
    last_ack_at: Mapped[datetime | None] = mapped_column("lastAckAt", DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column("lastSeenAt", DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column("expiresAt", DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column("revokedAt", DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column("createdAt", DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column("updatedAt", DateTime(timezone=True), nullable=False)


class SyncIdempotencyBatchRow(Base):
    __tablename__ = "sync_idempotency_batches"
    __table_args__ = (
        UniqueConstraint("id", "ownerId", name="uq_sync_batches_id_owner"),
        UniqueConstraint("ownerId", "deviceId", "idempotencyKey", name="uq_sync_batches_owner_device_key"),
        CheckConstraint('length("requestHash") = 64', name="ck_sync_batches_request_hash"),
        CheckConstraint("\"status\" IN ('processing', 'completed', 'failed')", name="ck_sync_batches_status"),
        CheckConstraint('"protocolVersion" >= 1', name="ck_sync_batches_protocol_version"),
        CheckConstraint(
            '("status" = \'processing\' AND "completedAt" IS NULL) OR '
            "(\"status\" IN ('completed', 'failed') AND \"completedAt\" IS NOT NULL "
            'AND "responseStatus" IS NOT NULL AND "responsePayload" IS NOT NULL)',
            name="ck_sync_batches_complete_response",
        ),
        Index("ix_sync_batches_expiry", "expiresAt", "status"),
        Index("ix_sync_batches_owner_created", "ownerId", "createdAt"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column("ownerId", ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    device_id: Mapped[str] = mapped_column("deviceId", String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column("idempotencyKey", String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column("requestHash", String(64), nullable=False)
    protocol_version: Mapped[int] = mapped_column("protocolVersion", Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    request_id: Mapped[str] = mapped_column("requestId", String(64), nullable=False)
    response_status: Mapped[int | None] = mapped_column("responseStatus", Integer, nullable=True)
    response_payload: Mapped[dict[str, Any] | None] = mapped_column("responsePayload", JSONB, nullable=True)
    expires_at: Mapped[datetime] = mapped_column("expiresAt", DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column("createdAt", DateTime(timezone=True), nullable=False)
    last_replayed_at: Mapped[datetime] = mapped_column("lastReplayedAt", DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column("completedAt", DateTime(timezone=True), nullable=True)
    operation_results: Mapped[list[SyncOperationResultRow]] = relationship(
        back_populates="batch",
        cascade="all, delete-orphan",
        order_by="SyncOperationResultRow.ordinal",
    )


class SyncOperationResultRow(Base):
    __tablename__ = "sync_operation_results"
    __table_args__ = (
        ForeignKeyConstraint(
            ["batchId", "ownerId"],
            ["sync_idempotency_batches.id", "sync_idempotency_batches.ownerId"],
            ondelete="CASCADE",
            name="fk_sync_operation_results_batch_owner",
        ),
        UniqueConstraint("batchId", "ordinal", name="uq_sync_operation_results_batch_ordinal"),
        UniqueConstraint("ownerId", "operationId", name="uq_sync_operation_results_owner_operation"),
        CheckConstraint('"ordinal" >= 0', name="ck_sync_operation_results_ordinal"),
        CheckConstraint(
            "\"status\" IN ('applied', 'noop', 'conflict', 'rejected', 'dependency_failed')",
            name="ck_sync_operation_results_status",
        ),
        Index("ix_sync_operation_results_operation", "operationId"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column("batchId", BigInteger, nullable=False)
    owner_id: Mapped[int] = mapped_column("ownerId", Integer, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    operation_id: Mapped[str] = mapped_column("operationId", String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    entity_type: Mapped[str | None] = mapped_column("entityType", String(32), nullable=True)
    public_id: Mapped[str | None] = mapped_column("publicId", String(36), nullable=True)
    accepted_version: Mapped[int | None] = mapped_column("acceptedVersion", BigInteger, nullable=True)
    content_hash: Mapped[str | None] = mapped_column("contentHash", String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column("errorCode", String(64), nullable=True)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    batch: Mapped[SyncIdempotencyBatchRow] = relationship(back_populates="operation_results")


DATABASE_URL = get_database_url(required=False)
engine = create_engine(DATABASE_URL, future=True, pool_pre_ping=True, pool_size=20, max_overflow=10) if DATABASE_URL else None
_SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False) if engine is not None else None


def SessionLocal():
    if _SessionLocal is None:
        raise RuntimeError("DATABASE_URL is required before database access.")
    return _SessionLocal()
