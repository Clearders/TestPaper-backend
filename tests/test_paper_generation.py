from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from testpaper_backend.schemas import Difficulty, GenerationTypeTarget, PaperGenerateRequest, QuestionType
from testpaper_backend.services import paper_generation


def _question_row(question_id: int, question_type: str, *, options: list[str] | None = None) -> SimpleNamespace:
    now = datetime(2026, 5, 19, tzinfo=UTC)
    return SimpleNamespace(
        id=question_id,
        public_id=f'q-{question_id}',
        type=question_type,
        subject="assas",
        difficulty=Difficulty.easy.value,
        tags=[],
        text=f"Question {question_id}",
        options=options,
        answer="A",
        has_latex=False,
        source=None,
        essay_blank_space=None,
        images=[],
        score_weight=1.0,
        owner_id=1,
        created_at=now,
        updated_at=now,
    )


def _generation_payload() -> PaperGenerateRequest:
    return PaperGenerateRequest(
        title="Generated",
        subject="assas",
        duration=60,
        totalMarks=10,
        difficultyCoefficient=0.5,
        questionTypes=[GenerationTypeTarget(questionType=QuestionType.single_choice, count=10)],
    )


def _session_factory(rows: list[SimpleNamespace]):
    class FakeScalarResult:
        def all(self) -> list[SimpleNamespace]:
            return rows

    class FakeSession:
        def __enter__(self) -> FakeSession:
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def scalars(self, _statement: Any) -> FakeScalarResult:
            return FakeScalarResult()

    return FakeSession


def test_build_generation_candidates_matches_database_string_type_alias(monkeypatch) -> None:
    rows = [
        _question_row(1, "选择题", options=["A", "B", "C", "D"]),
        _question_row(2, "QuestionType.essay"),
    ]
    monkeypatch.setattr(paper_generation, "SessionLocal", _session_factory(rows))

    candidates = paper_generation.build_generation_candidates(_generation_payload(), owner_id=1)

    assert [question.id for question in candidates] == [1]
    assert candidates[0].type == QuestionType.single_choice
