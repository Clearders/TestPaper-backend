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
        options=["A", "B"]
        if question_type in (QuestionType.single_choice, QuestionType.multiple_choice, QuestionType.true_false)
        else None,
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
    monkeypatch.setattr(
        papers,
        "QUESTIONS",
        SimpleNamespace(
            get=questions.get,
            get_by_public_id=lambda pid: questions.get(int(pid[2:])),
            get_by_public_ids=lambda public_ids: {pid: questions[int(pid[2:])] for pid in public_ids},
        ),
    )

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


def test_order_export_questions_groups_draft_snapshots_by_type() -> None:
    draft_questions = [
        {"id": 1, "orderNo": 1, "type": "essay", "text": "essay"},
        {"id": 2, "orderNo": 2, "type": "single_choice", "text": "choice"},
        {"id": 3, "orderNo": 3, "type": "blank", "text": "blank"},
    ]

    paper_order = papers.order_export_questions(draft_questions, QuestionOrder.paper)
    categorized = papers.order_export_questions(draft_questions, QuestionOrder.categorized)

    assert [question["id"] for question in paper_order] == [1, 2, 3]
    assert [question["id"] for question in categorized] == [2, 3, 1]


def test_replace_paper_question_refs_updates_existing_paper_without_duplicate(monkeypatch) -> None:
    questions = {
        "q-1": _question(1, QuestionType.single_choice, "choice first"),
        "q-2": _question(2, QuestionType.short_answer, "short answer"),
    }
    stored_papers = {
        1: PaperEntity(
            id=1,
            publicId="p-1",
            title="Existing Draft",
            subject="Math",
            duration=60,
            totalMarks=100,
            questions=[QuestionRef(questionPublicId="q-1", orderNo=1, marks=5)],
            createdAt=datetime(2026, 5, 19, tzinfo=UTC),
            updatedAt=datetime(2026, 5, 19, tzinfo=UTC),
        )
    }

    class FakePaperStore:
        def __setitem__(self, paper_id: int, paper: PaperEntity) -> None:
            stored_papers[paper_id] = paper.model_copy(update={"id": paper_id})

        def create(self, paper: PaperEntity) -> PaperEntity:
            raise AssertionError("editing an existing draft must not create a new paper")

    monkeypatch.setattr(papers, "PAPERS", FakePaperStore())
    monkeypatch.setattr(papers, "get_question_or_404", questions.get)

    updated = papers.replace_paper_question_refs(
        stored_papers[1],
        [
            QuestionRef(questionPublicId="q-2", orderNo=1, marks=7),
            QuestionRef(questionPublicId="q-1", orderNo=2, marks=5),
        ],
    )

    assert len(stored_papers) == 1
    assert updated.publicId == "p-1"
    assert [item.questionPublicId for item in stored_papers[1].questions] == ["q-2", "q-1"]
    assert [item.orderNo for item in stored_papers[1].questions] == [1, 2]
    assert [item.marks for item in stored_papers[1].questions] == [7, 5]
