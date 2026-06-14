"""initial schema

Revision ID: 20260507_0001
Revises:
Create Date: 2026-05-07 00:01:00
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260507_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def sql_str(value: str | None) -> str:
    if value is None:
        return "NULL"
    return "'" + value.replace("'", "''") + "'"


def sql_json(value: object | None) -> str:
    if value is None:
        return "NULL"
    return sql_str(json.dumps(value)) + "::jsonb"


def sql_bool(value: bool) -> str:
    return "TRUE" if value else "FALSE"


def sql_ts(value: datetime) -> str:
    return sql_str(value.isoformat())


def insert_seed_questions(now: datetime) -> None:
    questions = [
        {
            "id": 1,
            "type": "single_choice",
            "subjects": ["Mathematics"],
            "difficulty": "easy",
            "tags": ["algebra"],
            "text": "What is the solution for $x$: $2x + 5 = 13$?",
            "options": ["$x = 4$", "$x = 5$", "$x = 6$", "$x = 7$"],
            "answer": "$x = 4$",
            "has_latex": True,
            "source": "Built-in sample",
            "essay_blank_space": None,
            "images": [],
            "owner_id": None,
            "created_at": now - timedelta(days=6),
        },
        {
            "id": 2,
            "type": "blank",
            "subjects": ["Mathematics"],
            "difficulty": "medium",
            "tags": ["calculus"],
            "text": "Evaluate the integral: $$\\int_0^1 x^2 \\, dx = \\_\\_\\_$$",
            "options": None,
            "answer": "$\\dfrac{1}{3}$",
            "has_latex": True,
            "source": "Built-in sample",
            "essay_blank_space": None,
            "images": [],
            "owner_id": None,
            "created_at": now - timedelta(days=5),
        },
        {
            "id": 3,
            "type": "essay",
            "subjects": ["Physics"],
            "difficulty": "medium",
            "tags": ["mechanics"],
            "text": "Newton's second law states $F = ma$. A body of mass $5\\,\\text{kg}$ experiences a net force of $20\\,\\text{N}$. Find its acceleration.",
            "options": None,
            "answer": "$a = 4\\,\\text{m/s}^2$",
            "has_latex": True,
            "source": "Built-in sample",
            "essay_blank_space": {"lines": 5, "lineHeight": 28},
            "images": [],
            "owner_id": None,
            "created_at": now - timedelta(days=4),
        },
        {
            "id": 4,
            "type": "essay",
            "subjects": ["Mathematics"],
            "difficulty": "hard",
            "tags": ["calculus", "integration"],
            "text": "Compute $\\displaystyle\\int_0^\\infty e^{-x^2}\\,dx$.",
            "options": None,
            "answer": "$\\dfrac{\\sqrt{\\pi}}{2}$",
            "has_latex": True,
            "source": "Built-in sample",
            "essay_blank_space": {"lines": 7, "lineHeight": 28},
            "images": [],
            "owner_id": None,
            "created_at": now - timedelta(days=3),
        },
        {
            "id": 5,
            "type": "blank",
            "subjects": ["Chemistry"],
            "difficulty": "easy",
            "tags": ["stoichiometry"],
            "text": "Balance the following equation: H2 + O2 -> H2O",
            "options": None,
            "answer": "2H2 + O2 -> 2H2O",
            "has_latex": False,
            "source": "Built-in sample",
            "essay_blank_space": None,
            "images": [],
            "owner_id": None,
            "created_at": now - timedelta(days=2),
        },
        {
            "id": 6,
            "type": "essay",
            "subjects": ["Physics"],
            "difficulty": "hard",
            "tags": ["electromagnetism"],
            "text": "Using Maxwell's equations, show that the speed of light in vacuum is $c = \\dfrac{1}{\\sqrt{\\mu_0 \\varepsilon_0}}$. What is its numerical value?",
            "options": None,
            "answer": "$c \\approx 3 \\times 10^8\\,\\text{m/s}$",
            "has_latex": True,
            "source": "Built-in sample",
            "essay_blank_space": {"lines": 9, "lineHeight": 28},
            "images": [],
            "owner_id": None,
            "created_at": now - timedelta(days=1),
        },
        {
            "id": 7,
            "type": "true_false",
            "subjects": ["Mathematics"],
            "difficulty": "easy",
            "tags": ["geometry"],
            "text": "All squares are rectangles.",
            "options": ["True", "False"],
            "answer": "True",
            "has_latex": False,
            "source": "Built-in sample",
            "essay_blank_space": None,
            "images": [],
            "owner_id": None,
            "created_at": now - timedelta(days=7),
        },
        {
            "id": 8,
            "type": "true_false",
            "subjects": ["Physics"],
            "difficulty": "easy",
            "tags": ["thermodynamics"],
            "text": "Heat always flows from a colder object to a hotter object.",
            "options": ["True", "False"],
            "answer": "False",
            "has_latex": False,
            "source": "Built-in sample",
            "essay_blank_space": None,
            "images": [],
            "owner_id": None,
            "created_at": now - timedelta(days=7),
        },
        {
            "id": 9,
            "type": "short_answer",
            "subjects": ["Chemistry"],
            "difficulty": "medium",
            "tags": ["atomic-structure"],
            "text": "What is the atomic number of Carbon?",
            "options": None,
            "answer": "6",
            "has_latex": False,
            "source": "Built-in sample",
            "essay_blank_space": None,
            "images": [],
            "owner_id": None,
            "created_at": now - timedelta(days=7),
        },
        {
            "id": 10,
            "type": "short_answer",
            "subjects": ["Mathematics"],
            "difficulty": "medium",
            "tags": ["algebra"],
            "text": "What is the slope of the line $y = 3x + 2$?",
            "options": None,
            "answer": "3",
            "has_latex": True,
            "source": "Built-in sample",
            "essay_blank_space": None,
            "images": [],
            "owner_id": None,
            "created_at": now - timedelta(days=7),
        },
    ]
    values = []
    for question in questions:
        values.append(
            "("
            f"{question['id']}, "
            f"{sql_str(question['type'])}, "
            f"{sql_json(question['subjects'])}, "
            f"{sql_str(question['difficulty'])}, "
            f"{sql_json(question['tags'])}, "
            f"{sql_str(question['text'])}, "
            f"{sql_json(question['options'])}, "
            f"{sql_str(question['answer'])}, "
            f"{sql_bool(bool(question['has_latex']))}, "
            f"{sql_str(question['source'])}, "
            f"{sql_json(question['essay_blank_space'])}, "
            f"{sql_ts(question['created_at'])}, "
            f"{sql_ts(now)}"
            ")"
        )
    op.execute(
        sa.text(
            """
            INSERT INTO questions
                (id, type, subjects, difficulty, tags, text, options, answer, has_latex, source, essay_blank_space, created_at, updated_at)
            VALUES
            """
            + ",\n".join(values)
        )
    )


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("displayName", sa.String(length=120), nullable=False),
        sa.Column("passwordHash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("isActive", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
    )
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=False)

    op.create_table(
        "questions",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("subjects", postgresql.JSONB(), nullable=False),
        sa.Column("difficulty", sa.String(length=16), nullable=False),
        sa.Column("tags", postgresql.JSONB(), nullable=False),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("options", postgresql.JSONB(), nullable=True),
        sa.Column("answer", sa.String(), nullable=False),
        sa.Column("has_latex", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("essay_blank_space", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_questions_difficulty"), "questions", ["difficulty"], unique=False)

    op.create_table(
        "papers",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("duration", sa.Integer(), nullable=False),
        sa.Column("totalMarks", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_papers_subject"), "papers", ["subject"], unique=False)

    op.create_table(
        "auth_tokens",
        sa.Column("token", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("token"),
    )
    op.create_index(op.f("ix_auth_tokens_expires_at"), "auth_tokens", ["expires_at"], unique=False)
    op.create_index(op.f("ix_auth_tokens_user_id"), "auth_tokens", ["user_id"], unique=False)

    op.create_table(
        "paper_questions",
        sa.Column("paper_id", sa.Integer(), nullable=False),
        sa.Column("question_id", sa.Integer(), nullable=False),
        sa.Column("orderNo", sa.Integer(), nullable=False),
        sa.Column("marks", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["question_id"], ["questions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("paper_id", "question_id"),
        sa.UniqueConstraint("paper_id", "orderNo", name="uq_paper_question_order"),
    )

    now = datetime.now(timezone.utc)
    insert_seed_questions(now)


def downgrade() -> None:
    op.drop_table("paper_questions")
    op.drop_index(op.f("ix_auth_tokens_user_id"), table_name="auth_tokens")
    op.drop_index(op.f("ix_auth_tokens_expires_at"), table_name="auth_tokens")
    op.drop_table("auth_tokens")
    op.drop_index(op.f("ix_papers_subject"), table_name="papers")
    op.drop_table("papers")
    op.drop_index(op.f("ix_questions_subject"), table_name="questions")
    op.drop_index(op.f("ix_questions_difficulty"), table_name="questions")
    op.drop_table("questions")
    op.drop_index(op.f("ix_users_username"), table_name="users")
    op.drop_table("users")
