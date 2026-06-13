from __future__ import annotations

from testpaper_backend.schemas import Difficulty
from testpaper_backend.services.paper_generation import (
    difficulty_targets_from_coefficient,
    distribute_marks,
    normalize_targets,
)


def test_distribute_marks_empty():
    assert distribute_marks([], 100) == []


def test_difficulty_targets_from_coefficient_easy():
    targets = difficulty_targets_from_coefficient(0.0, 10)
    assert targets[Difficulty.easy] > targets[Difficulty.hard]


def test_normalize_targets_rounding():
    targets: dict[str, int] = {"a": 3, "b": 3, "c": 3}
    normalized = normalize_targets(targets, 10)
    assert sum(normalized.values()) == 10
