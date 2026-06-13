from __future__ import annotations

import pytest

from testpaper_backend.repositories import has_latex, normalize_question_type
from testpaper_backend.schemas import Difficulty, QuestionBase, QuestionType


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("选择", QuestionType.single_choice),
        ("选择题", QuestionType.single_choice),
        ("单选题", QuestionType.single_choice),
        ("multiple_choice", QuestionType.multiple_choice),
        ("多选题", QuestionType.multiple_choice),
        ("判断", QuestionType.true_false),
        ("填空", QuestionType.blank),
        ("简答", QuestionType.short_answer),
        ("问答题", QuestionType.essay),
        ("论述", QuestionType.essay),
        ("unknown_type", QuestionType("unknown_type")),
    ],
)
def test_normalize_question_type(raw, expected):
    assert normalize_question_type(raw) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("$x^2$ is quadratic", True),
        ("$$\nx^2\n$$", True),
        ("plain text", False),
        ("", False),
    ],
)
def test_has_latex_content(text, expected):
    q = QuestionBase(
        type=QuestionType.short_answer,
        subjects=["math"],
        difficulty=Difficulty.easy,
        text=text,
        answer="2",
    )
    assert has_latex(q) == expected
