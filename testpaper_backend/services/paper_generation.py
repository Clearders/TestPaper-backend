from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass
from itertools import chain
from typing import Any

from fastapi import status
from sqlalchemy import ARRAY, String, select, type_coerce

from testpaper_backend.core.errors import api_error, validation_error
from testpaper_backend.db import QuestionRow, SessionLocal
from testpaper_backend.repositories import normalize_question_type, question_row_to_entity
from testpaper_backend.schemas import Difficulty, PaperGenerateRequest, QuestionEntity, QuestionRef, QuestionType


@dataclass(frozen=True)
class GenerationQuestionFeatures:
    difficulty: Difficulty
    question_type: QuestionType
    tags: frozenset[str]
    subjects: frozenset[str]
    score_weight: float


@dataclass(frozen=True)
class GeneticAlgorithmOptions:
    populationSize: int = 80
    generations: int = 120
    crossoverRate: float = 0.85
    mutationRate: float = 0.08
    elitismCount: int = 4
    tournamentSize: int = 3
    randomSeed: int | None = None


def distribute_marks(questions: list[QuestionEntity], total_marks: int) -> list[int]:
    if not questions:
        return []
    if total_marks < len(questions):
        raise ValueError("total_marks must be at least the number of questions")
    weights = [max(0.01, question.scoreWeight) for question in questions]
    weight_total = sum(weights) or 1.0
    raw_marks = [max(1, total_marks * weight / weight_total) for weight in weights]
    marks = [max(1, int(value)) for value in raw_marks]
    remaining = total_marks - sum(marks)

    ranked = sorted(
        enumerate(raw_marks),
        key=lambda item: item[1] - int(item[1]),
        reverse=remaining > 0,
    )
    index = 0
    attempts = 0
    max_attempts = max(1, len(ranked) * max(total_marks, sum(marks)))
    while remaining != 0 and ranked and attempts < max_attempts:
        question_index, _ = ranked[index % len(ranked)]
        if remaining > 0:
            marks[question_index] += 1
            remaining -= 1
        elif marks[question_index] > 1:
            marks[question_index] -= 1
            remaining += 1
        index += 1
        attempts += 1

    return marks


def difficulty_targets_from_coefficient(coefficient: float, question_count: int) -> dict[Difficulty, int]:
    anchors = {
        Difficulty.easy: 0.0,
        Difficulty.medium: 0.5,
        Difficulty.hard: 1.0,
    }
    weights = {
        difficulty: max(0.0, 1.0 - abs(coefficient - anchor) * 2)
        for difficulty, anchor in anchors.items()
    }
    if not any(weights.values()):
        weights[Difficulty.medium] = 1.0
    raw_targets = {difficulty: round(question_count * weight) for difficulty, weight in weights.items()}
    if sum(raw_targets.values()) <= 0:
        nearest = min(anchors, key=lambda difficulty: abs(coefficient - anchors[difficulty]))
        raw_targets[nearest] = question_count
    return normalize_targets(raw_targets, question_count)


def normalize_targets[T](targets: dict[T, int], question_count: int) -> dict[T, int]:
    total = sum(targets.values())
    if not targets or total == question_count:
        return targets
    if total <= 0:
        first_key = next(iter(targets))
        return {key: question_count if key == first_key else 0 for key in targets}

    normalized = {key: round(question_count * value / total) for key, value in targets.items()}
    difference = question_count - sum(normalized.values())
    ordered_keys = sorted(targets, key=lambda key: targets[key], reverse=difference > 0)
    index = 0
    while difference != 0 and ordered_keys:
        key = ordered_keys[index % len(ordered_keys)]
        if difference > 0:
            normalized[key] += 1
            difference -= 1
        elif normalized[key] > 0:
            normalized[key] -= 1
            difference += 1
        index += 1
    return normalized


def generation_type_counts(payload: PaperGenerateRequest) -> dict[QuestionType, int]:
    counts: dict[QuestionType, int] = {}
    for target in payload.questionTypes:
        counts[target.questionType] = counts.get(target.questionType, 0) + target.count
    return counts


# ---- Phase 1: Candidate Selection ----

def build_generation_candidates(payload: PaperGenerateRequest, owner_id: int | None = None) -> list[QuestionEntity]:
    subjects = [s.strip() for s in payload.subjects if s.strip()]
    if not subjects:
        raise ValueError("At least one subject is required")
    selected_types = set(generation_type_counts(payload))
    statement = select(QuestionRow).where(
        QuestionRow.subjects.op('?|')(type_coerce(subjects, ARRAY(String))),
    )
    if owner_id is not None:
        statement = statement.where(QuestionRow.owner_id == owner_id)
    with SessionLocal() as session:
        rows = session.scalars(statement.order_by(QuestionRow.id)).all()
        candidates = []
        for row in rows:
            try:
                row_type = normalize_question_type(row.type)
            except ValueError:
                continue
            if row_type in selected_types:
                candidates.append(question_row_to_entity(row))
    if not candidates:
        subject_str = ", ".join(payload.subjects)
        source_message = " in your question bank" if owner_id is not None else ""
        type_names = ", ".join(t.value for t in selected_types)
        raise api_error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "INSUFFICIENT_QUESTIONS",
            f"Need at least 1 question of types [{type_names}] for {subject_str}{source_message}, found 0.",
            {
                "subject": subject_str,
                "questionTypes": [t.value for t in selected_types],
                "candidateCount": 0,
                "ownQuestionsOnly": owner_id is not None,
            },
        )
    return candidates


def build_generation_features(questions: list[QuestionEntity]) -> dict[int, GenerationQuestionFeatures]:
    return {
        question.id: GenerationQuestionFeatures(
            difficulty=question.difficulty,
            question_type=question.type,
            tags=frozenset(tag.lower() for tag in question.tags),
            subjects=frozenset(s.lower() for s in question.subjects),
            score_weight=max(0.01, question.scoreWeight),
        )
        for question in questions
    }


# ---- Phase 2: Fitness Evaluation ----

def individual_fitness(
    individual: list[int],
    question_features: dict[int, GenerationQuestionFeatures],
    difficulty_targets: dict[Difficulty, int],
    type_targets: dict[QuestionType, int],
    required_tags: set[str],
    optional_tags: set[str],
    target_score_weight: float | None = None,
) -> float:
    difficulty_counts: Counter[Difficulty] = Counter()
    type_counts: Counter[QuestionType] = Counter()
    tags: set[str] = set()
    subjects: set[str] = set()
    score_weight_total = 0.0
    for question_id in individual:
        features = question_features[question_id]
        difficulty_counts[features.difficulty] += 1
        type_counts[features.question_type] += 1
        subjects.update(features.subjects)
        tags.update(features.tags)
        score_weight_total += features.score_weight

    penalty = 0.0
    for difficulty, target in difficulty_targets.items():
        penalty += abs(difficulty_counts[difficulty] - target) * 40
    for question_type, target in type_targets.items():
        penalty += abs(type_counts[question_type] - target) * 30
    penalty += len(required_tags - tags) * 80
    if target_score_weight is not None:
        penalty += abs(score_weight_total - target_score_weight) * 8

    optional_tag_bonus = len(optional_tags & tags) * 24
    diversity_bonus = min(len(tags), 10) * 2 + min(len(subjects), 3) * 3
    return 1000 - penalty + optional_tag_bonus + diversity_bonus


def crossover_individual(
    parent_a: list[int],
    parent_b: list[int],
    candidate_ids: list[int],
    question_count: int,
    rng: random.Random,
) -> list[int]:
    pivot = rng.randint(1, question_count - 1) if question_count > 1 else 1
    child = parent_a[:pivot]
    child_ids = set(child)
    for question_id in chain(parent_b, candidate_ids):
        if len(child) >= question_count:
            break
        if question_id not in child_ids:
            child.append(question_id)
            child_ids.add(question_id)
    return child


def mutate_individual(individual: list[int], candidate_ids: list[int], mutation_rate: float, rng: random.Random) -> list[int]:
    mutated = individual[:]
    if not candidate_ids:
        return mutated
    selected_ids = set(mutated)
    for index, current_id in enumerate(mutated):
        if rng.random() >= mutation_rate:
            continue
        if len(selected_ids) >= len(candidate_ids):
            continue
        selected_ids.remove(current_id)
        replacement_id = rng.choice(candidate_ids)
        while replacement_id == current_id or replacement_id in selected_ids:
            replacement_id = rng.choice(candidate_ids)
        mutated[index] = replacement_id
        selected_ids.add(replacement_id)
    return mutated


def build_generation_result(
    selected_questions: list[QuestionEntity],
    *,
    total_marks: int,
    fitness: float,
    candidate_count: int,
    question_count: int,
    owner_id: int | None,
    difficulty_coefficient: float,
    difficulty_targets: dict[Difficulty, int],
    type_targets: dict[QuestionType, int],
    type_adjustments: list[dict[str, Any]],
    generations_run: int,
    required_tags: set[str],
    optional_tags: set[str],
) -> dict[str, Any]:
    marks = distribute_marks(selected_questions, total_marks)
    return {
        "paperQuestions": [
            QuestionRef(questionPublicId=question.publicId, orderNo=index + 1, marks=marks[index])
            for index, question in enumerate(selected_questions)
        ],
        "selectedQuestions": selected_questions,
        "diagnostics": {
            "fitness": round(fitness, 2),
            "candidateCount": candidate_count,
            "questionCount": question_count,
            "ownQuestionsOnly": owner_id is not None,
            "difficultyCoefficient": difficulty_coefficient,
            "scoreWeightActual": round(sum(max(0.01, question.scoreWeight) for question in selected_questions), 2),
            "marksActual": sum(marks),
            "difficultyTargets": {key.value: value for key, value in difficulty_targets.items()},
            "difficultyActual": dict(Counter(question.difficulty.value for question in selected_questions)),
            "typeTargets": {key.value: value for key, value in type_targets.items()},
            "typeActual": dict(Counter(question.type.value for question in selected_questions)),
            "typeAdjustments": type_adjustments,
            "generationsRun": generations_run,
            "requiredTags": list(required_tags),
            "preferredTags": list(optional_tags),
        },
    }


# ---- Phase 3: Genetic Algorithm Loop ----

def generate_paper_with_genetic_algorithm(payload: PaperGenerateRequest, owner_id: int | None = None) -> dict[str, Any]:
    candidates = build_generation_candidates(payload, owner_id=owner_id)
    question_by_id = {question.id: question for question in candidates}
    question_features = build_generation_features(candidates)
    candidate_ids = list(question_by_id)
    algorithm = GeneticAlgorithmOptions()
    rng = random.Random(algorithm.randomSeed)
    type_targets: dict[QuestionType, int] = {}
    type_adjustments: list[dict[str, Any]] = []
    for question_type, requested_count in generation_type_counts(payload).items():
        available = sum(1 for q in candidates if q.type == question_type)
        target = min(requested_count, available)
        type_targets[question_type] = target
        if target < requested_count:
            type_adjustments.append({
                "type": question_type.value,
                "requested": requested_count,
                "available": available,
                "adjusted": target,
            })
    question_count = sum(type_targets.values())
    if len(candidates) < question_count:
        raise api_error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "INSUFFICIENT_QUESTIONS",
            f"Need at least {question_count} candidate questions, found {len(candidates)}.",
        )
    if payload.totalMarks < question_count:
        raise validation_error("totalMarks must be at least the number of selected questions")
    difficulty_targets = difficulty_targets_from_coefficient(payload.difficultyCoefficient, question_count)
    required_tags: set[str] = {tag.lower().strip() for tag in (payload.requiredTags or []) if tag and tag.strip()}
    optional_tags: set[str] = {tag.lower().strip() for tag in (payload.preferredTags or []) if tag and tag.strip()} - required_tags
    population_size = max(algorithm.populationSize, algorithm.elitismCount + algorithm.tournamentSize)
    population_size = min(population_size, max(1, len(candidate_ids) * 8))
    target_score_weight = payload.totalMarks

    if len(candidates) == question_count:
        selected_questions = candidates
        result = build_generation_result(
            selected_questions,
            total_marks=payload.totalMarks,
            fitness=1000,
            candidate_count=len(candidates),
            question_count=question_count,
            owner_id=owner_id,
            difficulty_coefficient=payload.difficultyCoefficient,
            difficulty_targets=difficulty_targets,
            type_targets=type_targets,
            type_adjustments=type_adjustments,
            generations_run=0,
            required_tags=required_tags,
            optional_tags=optional_tags,
        )
        return result

    population = [rng.sample(candidate_ids, question_count) for _ in range(population_size)]
    best = population[0]
    best_score = float("-inf")
    generations_without_improvement = 0
    generations_run = 0

    def score(individual: list[int]) -> float:
        return individual_fitness(
            individual,
            question_features,
            difficulty_targets,
            type_targets,
            required_tags,
            optional_tags,
            target_score_weight,
        )

    for generation in range(algorithm.generations):
        generations_run = generation + 1
        ranked = sorted(((score(individual), individual) for individual in population), key=lambda item: item[0], reverse=True)
        if ranked[0][0] > best_score:
            best_score, best = ranked[0][0], ranked[0][1][:]
            generations_without_improvement = 0
        else:
            generations_without_improvement += 1
        if generations_without_improvement >= 30 and generation >= 50:
            break

        elite_count = min(algorithm.elitismCount, len(ranked))
        next_population = [individual[:] for _, individual in ranked[:elite_count]]

        while len(next_population) < population_size:
            tournament = rng.sample(ranked, min(algorithm.tournamentSize, len(ranked)))
            parent_a = max(tournament, key=lambda item: item[0])[1]
            tournament = rng.sample(ranked, min(algorithm.tournamentSize, len(ranked)))
            parent_b = max(tournament, key=lambda item: item[0])[1]

            if rng.random() < algorithm.crossoverRate:
                child = crossover_individual(parent_a, parent_b, candidate_ids, question_count, rng)
            else:
                child = parent_a[:]
            next_population.append(mutate_individual(child, candidate_ids, algorithm.mutationRate, rng))

        population = next_population

    selected_questions = [question_by_id[question_id] for question_id in best]
    result = build_generation_result(
        selected_questions,
        total_marks=payload.totalMarks,
        fitness=best_score,
        candidate_count=len(candidates),
        question_count=question_count,
        owner_id=owner_id,
        difficulty_coefficient=payload.difficultyCoefficient,
        difficulty_targets=difficulty_targets,
        type_targets=type_targets,
        type_adjustments=type_adjustments,
        generations_run=generations_run,
        required_tags=required_tags,
        optional_tags=optional_tags,
    )
    return result
