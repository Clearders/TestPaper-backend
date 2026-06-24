from __future__ import annotations

import pytest

from testpaper_backend.schemas import Difficulty, PaperGenerateRequest, QuestionType
from testpaper_backend.services.paper_generation import (
    difficulty_targets_from_coefficient,
    distribute_marks,
    generation_type_counts,
    normalize_targets,
)


def test_distribute_marks_empty():
    assert distribute_marks([], 100) == []


def test_distribute_marks_rejects_impossible_positive_marks():
    with pytest.raises(ValueError, match="at least"):
        questions = [object(), object()]
        distribute_marks(questions, 1)


def test_generation_type_counts_combines_duplicate_targets():
    payload = PaperGenerateRequest(
        title="Paper",
        duration=60,
        totalMarks=10,
        difficultyCoefficient=0.5,
        subjects=["Math"],
        questionTypes=[
            {"questionType": "single_choice", "count": 2},
            {"questionType": "single_choice", "count": 3},
            {"questionType": "blank", "count": 1},
        ],
    )

    assert generation_type_counts(payload) == {
        QuestionType.single_choice: 5,
        QuestionType.blank: 1,
    }


def test_difficulty_targets_from_coefficient_easy():
    targets = difficulty_targets_from_coefficient(0.0, 10)
    assert targets[Difficulty.easy] > targets[Difficulty.hard]


def test_normalize_targets_rounding():
    targets: dict[str, int] = {"a": 3, "b": 3, "c": 3}
    normalized = normalize_targets(targets, 10)
    assert sum(normalized.values()) == 10
