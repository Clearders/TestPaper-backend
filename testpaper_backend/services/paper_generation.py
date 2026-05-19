from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass
from itertools import chain
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select

from testpaper_backend.db import QuestionRow, SessionLocal
from testpaper_backend.repositories import normalize_question_type, question_row_to_entity
from testpaper_backend.schemas import Difficulty, PaperGenerateRequest, PaperQuestion, QuestionEntity, QuestionType


@dataclass(frozen=True)
class GenerationQuestionFeatures:
    difficulty: Difficulty
    question_type: QuestionType
    tags: frozenset[str]
    subject: str
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


def resolve_generation_question_count(payload: PaperGenerateRequest, candidates: list[QuestionEntity]) -> int:
    average_weight = sum(max(0.01, question.scoreWeight) for question in candidates) / len(candidates)
    estimated_count = max(1, round(payload.totalMarks / max(0.01, average_weight)))
    return max(1, min(estimated_count, payload.totalMarks, len(candidates), 100))


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


def build_generation_candidates(payload: PaperGenerateRequest, owner_id: int | None = None) -> list[QuestionEntity]:
    subject = payload.subject.strip()
    statement = select(QuestionRow).where(
        func.lower(func.trim(QuestionRow.subject)) == subject.lower(),
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
            if row_type == payload.questionType:
                candidates.append(question_row_to_entity(row))
    if not candidates:
        source_message = " in your question bank" if owner_id is not None else ""
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "INSUFFICIENT_QUESTIONS",
                "message": f"Need at least 1 {payload.questionType.value} question for {subject}{source_message}, found 0.",
                "details": {
                    "subject": subject,
                    "questionType": payload.questionType.value,
                    "candidateCount": 0,
                    "ownQuestionsOnly": owner_id is not None,
                },
            },
        )
    return candidates


def build_generation_features(questions: list[QuestionEntity]) -> dict[int, GenerationQuestionFeatures]:
    return {
        question.id: GenerationQuestionFeatures(
            difficulty=question.difficulty,
            question_type=question.type,
            tags=frozenset(tag.lower() for tag in question.tags),
            subject=question.subject,
            score_weight=max(0.01, question.scoreWeight),
        )
        for question in questions
    }


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
        subjects.add(features.subject)
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


def generate_paper_with_genetic_algorithm(payload: PaperGenerateRequest, owner_id: int | None = None) -> dict[str, Any]:
    candidates = build_generation_candidates(payload, owner_id=owner_id)
    question_by_id = {question.id: question for question in candidates}
    question_features = build_generation_features(candidates)
    candidate_ids = list(question_by_id)
    algorithm = GeneticAlgorithmOptions()
    rng = random.Random(algorithm.randomSeed)
    question_count = resolve_generation_question_count(payload, candidates)
    if len(candidates) < question_count:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "INSUFFICIENT_QUESTIONS",
                "message": f"Need at least {question_count} candidate questions, found {len(candidates)}.",
            },
        )
    difficulty_targets = difficulty_targets_from_coefficient(payload.difficultyCoefficient, question_count)
    type_targets = {payload.questionType: question_count}
    required_tags: set[str] = set()
    optional_tags: set[str] = set()
    population_size = max(algorithm.populationSize, algorithm.elitismCount + algorithm.tournamentSize)
    population_size = min(population_size, max(1, len(candidate_ids) * 8))
    target_score_weight = payload.totalMarks

    if len(candidates) == question_count:
        selected_questions = candidates
        marks = distribute_marks(selected_questions, payload.totalMarks)
        return {
            "paperQuestions": [
                PaperQuestion(questionId=question.id, orderNo=index + 1, marks=marks[index])
                for index, question in enumerate(selected_questions)
            ],
            "selectedQuestions": selected_questions,
            "diagnostics": {
                "fitness": 1000,
                "candidateCount": len(candidates),
                "questionCount": question_count,
                "ownQuestionsOnly": owner_id is not None,
                "difficultyCoefficient": payload.difficultyCoefficient,
                "scoreWeightActual": round(sum(max(0.01, question.scoreWeight) for question in selected_questions), 2),
                "marksActual": sum(marks),
                "difficultyTargets": {key.value: value for key, value in difficulty_targets.items()},
                "difficultyActual": dict(Counter(question.difficulty.value for question in selected_questions)),
                "typeTargets": {key.value: value for key, value in type_targets.items()},
                "typeActual": dict(Counter(question.type.value for question in selected_questions)),
                "generationsRun": 0,
            },
        }

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
    marks = distribute_marks(selected_questions, payload.totalMarks)
    paper_questions = [
        PaperQuestion(questionId=question.id, orderNo=index + 1, marks=marks[index])
        for index, question in enumerate(selected_questions)
    ]
    diagnostics = {
        "fitness": round(best_score, 2),
        "candidateCount": len(candidates),
        "questionCount": question_count,
        "ownQuestionsOnly": owner_id is not None,
        "difficultyCoefficient": payload.difficultyCoefficient,
        "scoreWeightActual": round(sum(max(0.01, question.scoreWeight) for question in selected_questions), 2),
        "marksActual": sum(marks),
        "difficultyTargets": {key.value: value for key, value in difficulty_targets.items()},
        "difficultyActual": dict(Counter(question.difficulty.value for question in selected_questions)),
        "typeTargets": {key.value: value for key, value in type_targets.items()},
        "typeActual": dict(Counter(question.type.value for question in selected_questions)),
        "generationsRun": generations_run,
    }
    return {
        "paperQuestions": paper_questions,
        "selectedQuestions": selected_questions,
        "diagnostics": diagnostics,
    }
