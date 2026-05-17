from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from testpaper_backend.config import get_database_url
from testpaper_backend.schemas import PaperStatus, UserRole


class Base(DeclarativeBase):
    pass


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    display_name: Mapped[str] = mapped_column("displayName", String(120), nullable=False)
    password_hash: Mapped[str] = mapped_column("passwordHash", String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default=UserRole.viewer.value)
    is_active: Mapped[bool] = mapped_column("isActive", Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    tokens: Mapped[list[AuthTokenRow]] = relationship(back_populates="user", cascade="all, delete-orphan")


class AuthTokenRow(Base):
    __tablename__ = "auth_tokens"

    token: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    user: Mapped[UserRow] = relationship(back_populates="tokens")


class QuestionRow(Base):
    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    difficulty: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    text: Mapped[str] = mapped_column(String, nullable=False)
    options: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    answer: Mapped[str] = mapped_column(String, nullable=False)
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
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    duration: Mapped[int] = mapped_column(Integer, nullable=False)
    total_marks: Mapped[int] = mapped_column("totalMarks", Integer, nullable=False)
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
    __table_args__ = (
        UniqueConstraint("paper_id", "orderNo", name="uq_paper_question_order"),
    )

    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"), primary_key=True)
    order_no: Mapped[int] = mapped_column("orderNo", Integer, nullable=False)
    marks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    paper: Mapped[PaperRow] = relationship(back_populates="questions")


DATABASE_URL = get_database_url(required=False)
engine = create_engine(DATABASE_URL, future=True, pool_pre_ping=True) if DATABASE_URL else None
_SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False) if engine is not None else None


def SessionLocal():
    if _SessionLocal is None:
        raise RuntimeError("DATABASE_URL is required before database access.")
    return _SessionLocal()
