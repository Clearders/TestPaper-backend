from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, create_engine
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
    bank: Mapped[QuestionBankRow] = relationship(back_populates="publications")
    created_by_user: Mapped[UserRow | None] = relationship(foreign_keys=[created_by])


class BankSubscriptionRow(Base):
    __tablename__ = "bank_subscriptions"

    bank_id: Mapped[int] = mapped_column("bankId", ForeignKey("question_banks.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column("userId", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    bank: Mapped[QuestionBankRow] = relationship(back_populates="subscriptions")
    user: Mapped[UserRow] = relationship()


DATABASE_URL = get_database_url(required=False)
engine = create_engine(DATABASE_URL, future=True, pool_pre_ping=True, pool_size=20, max_overflow=10) if DATABASE_URL else None
_SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False) if engine is not None else None


def SessionLocal():
    if _SessionLocal is None:
        raise RuntimeError("DATABASE_URL is required before database access.")
    return _SessionLocal()
