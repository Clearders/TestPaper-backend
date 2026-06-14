from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from testpaper_backend.schemas import Difficulty, PaperEntity, QuestionEntity, QuestionOrder, QuestionRef, QuestionType
from testpaper_backend.services import papers


def _question(question_id: int, question_type: QuestionType, text: str) -> QuestionEntity:
    return QuestionEntity(
        id=question_id,
        publicId=f"q-{question_id}",
        type=question_type,
        subjects=["Math"],
        difficulty=Difficulty.easy,
        tags=[],
        text=text,
        options=["A", "B"] if question_type in (
            QuestionType.single_choice, QuestionType.multiple_choice, QuestionType.true_false
        ) else None,
        answer="A",
        createdAt=datetime(2026, 5, 19, tzinfo=UTC),
        updatedAt=datetime(2026, 5, 19, tzinfo=UTC),
    )


def test_build_export_questions_groups_by_type_after_paper_order(monkeypatch) -> None:
    questions = {
        1: _question(1, QuestionType.essay, "essay first"),
        2: _question(2, QuestionType.single_choice, "choice first"),
        3: _question(3, QuestionType.short_answer, "short answer"),
        4: _question(4, QuestionType.single_choice, "choice second"),
        5: _question(5, QuestionType.blank, "blank"),
    }
    monkeypatch.setattr(papers, "QUESTIONS", SimpleNamespace(
        get=questions.get,
        get_by_public_id=lambda pid: questions.get(int(pid[2:])),
    ))

    paper = PaperEntity(
        id=1,
        publicId="p-1",
        title="Grouped Export",
        subject="Math",
        duration=60,
        totalMarks=100,
        questions=[
            QuestionRef(questionPublicId="q-1", orderNo=1, marks=10),
            QuestionRef(questionPublicId="q-2", orderNo=2, marks=5),
            QuestionRef(questionPublicId="q-3", orderNo=3, marks=8),
            QuestionRef(questionPublicId="q-4", orderNo=4, marks=6),
            QuestionRef(questionPublicId="q-5", orderNo=5, marks=4),
        ],
        createdAt=datetime(2026, 5, 19, tzinfo=UTC),
        updatedAt=datetime(2026, 5, 19, tzinfo=UTC),
    )

    paper_order = papers.build_export_questions(paper, QuestionOrder.paper, include_answer=True)
    categorized = papers.build_export_questions(paper, QuestionOrder.categorized, include_answer=True)

    assert [question["id"] for question in paper_order] == [1, 2, 3, 4, 5]
    assert [question["id"] for question in categorized] == [2, 4, 5, 3, 1]
