from __future__ import annotations

import base64
import binascii
import json
import random
import secrets
from collections import Counter
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import chain
from math import ceil
from time import perf_counter
from typing import Any, cast
from urllib.parse import quote
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import String, cast as sql_cast, func, or_, select, text
from sqlalchemy.orm import Session

from app_factory import create_app
from db import AuthTokenRow, QuestionRow, SessionLocal, UserRow, engine
from paper_docx import DOCX_MEDIA_TYPE, build_paper_docx, docx_filename
from repositories import PAPERS, QUESTIONS, has_latex, question_row_to_entity
from schemas import (
    AuthSession,
    Difficulty,
    ExportPreviewRequest,
    GenerationAllocationMode,
    ImageUploadPayload,
    ImageUploadResponse,
    LoginRequest,
    PaperCreate,
    PaperEntity,
    PaperGenerateRequest,
    PaperQuestion,
    PaperStatus,
    PaperUpdate,
    QuestionBase,
    QuestionCreate,
    QuestionEntity,
    QuestionOrder,
    QuestionOrderUpdate,
    QuestionRef,
    QuestionType,
    QuestionUpdate,
    RegisterRequest,
    SortOrder,
    UserCreate,
    UserEntity,
    UserRole,
    UserUpdate,
)
from security import (
    auth_error,
    get_current_user,
    get_request_token,
    get_user_from_token,
    has_permission,
    password_hash,
    require_permission,
    user_row_to_entity,
    verify_password,
)
from settings import get_auth_cookie_domain, get_auth_cookie_name, get_auth_cookie_samesite, get_auth_cookie_secure
from time_utils import as_aware_utc, now_utc

SESSION_TTL = timedelta(hours=12)
MAX_IMAGE_UPLOAD_BYTES = 30 * 1024 * 1024
PNG_SIGNATURE = bytes((137, 80, 78, 71, 13, 10, 26, 10))


def normalize_question_payload(payload: QuestionBase, question_id: int, created_at: datetime | None = None) -> QuestionEntity:
    normalized = deepcopy(payload.model_dump())
    normalized["hasLatex"] = payload.hasLatex if payload.hasLatex is not None else has_latex(payload)
    return QuestionEntity(
        id=question_id,
        createdAt=created_at or now_utc(),
        updatedAt=now_utc(),
        **normalized,
    )


def create_auth_session(session: Session, user_row: UserRow) -> tuple[str, AuthSession]:
    now = now_utc()
    session.query(AuthTokenRow).filter(AuthTokenRow.expires_at <= now).delete(synchronize_session=False)
    token = secrets.token_urlsafe(48)
    expires_at = now + SESSION_TTL
    session.add(AuthTokenRow(token=token, user_id=user_row.id, created_at=now, expires_at=expires_at))
    session.commit()
    session.refresh(user_row)
    return token, AuthSession(expiresAt=expires_at, user=user_row_to_entity(user_row))


def set_auth_cookie(response: Response, token: str, expires_at: datetime) -> None:
    max_age = max(0, int((expires_at - now_utc()).total_seconds()))
    response.set_cookie(
        key=get_auth_cookie_name(),
        value=token,
        max_age=max_age,
        expires=max_age,
        path="/",
        domain=get_auth_cookie_domain(),
        secure=get_auth_cookie_secure(),
        httponly=True,
        samesite=get_auth_cookie_samesite(),
    )


def clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(
        key=get_auth_cookie_name(),
        path="/",
        domain=get_auth_cookie_domain(),
        secure=get_auth_cookie_secure(),
        httponly=True,
        samesite=get_auth_cookie_samesite(),
    )


def refresh_auth_session(token: str | None) -> tuple[str, AuthSession]:
    if not token:
        raise auth_error()

    with SessionLocal() as session:
        token_row = cast(AuthTokenRow | None, session.get(AuthTokenRow, token))
        if token_row is None:
            raise auth_error("INVALID_TOKEN", "Invalid or expired token")
        if as_aware_utc(token_row.expires_at) <= now_utc():
            session.delete(token_row)
            session.commit()
            raise auth_error("TOKEN_EXPIRED", "Token has expired")

        user_row = cast(UserRow | None, session.get(UserRow, token_row.user_id))
        if user_row is None or not user_row.is_active:
            session.delete(token_row)
            session.commit()
            raise auth_error("ACCOUNT_DISABLED", "Account is disabled")

        session.delete(token_row)
        session.flush()
        return create_auth_session(session, user_row)


class RealtimeConnectionManager:
    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self._connections.discard(websocket)

    async def broadcast(self, event: str, payload: dict[str, Any]) -> None:
        if not self._connections:
            return

        message = json.dumps({"event": event, "payload": payload}, default=str)
        stale: list[WebSocket] = []
        for websocket in list(self._connections):
            try:
                await websocket.send_text(message)
            except RuntimeError:
                stale.append(websocket)

        for websocket in stale:
            self.disconnect(websocket)


realtime = RealtimeConnectionManager()


def get_websocket_token(websocket: WebSocket) -> str | None:
    header = websocket.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() == "bearer" and token:
        return token
    return websocket.cookies.get(get_auth_cookie_name())


def apply_question_update(question: QuestionEntity, patch: QuestionUpdate) -> QuestionEntity:
    data = question.model_dump()
    patch_data = patch.model_dump(exclude_unset=True)
    data.update(patch_data)

    if patch_data.get("type") == QuestionType.choice or data.get("type") == QuestionType.choice or data.get("type") == QuestionType.true_false:
        options = data.get("options") or []
        data["options"] = [option.strip() for option in options if option and option.strip()]
    elif data.get("type") not in (QuestionType.choice, QuestionType.true_false):
        data["options"] = None

    if data.get("type") == QuestionType.essay:
        if data.get("essayBlankSpace") is None:
            data["essayBlankSpace"] = {"lines": 6, "lineHeight": 28}
    else:
        data["essayBlankSpace"] = None

    data["tags"] = [tag.strip() for tag in (data.get("tags") or []) if tag and tag.strip()]
    if data.get("source") is not None:
        data["source"] = data["source"].strip() or None

    try:
        normalized = QuestionCreate(
            type=data["type"],
            subject=data["subject"],
            difficulty=data["difficulty"],
            tags=data.get("tags") or [],
            text=data["text"],
            options=data.get("options"),
            answer=data["answer"],
            source=data.get("source"),
            essayBlankSpace=data.get("essayBlankSpace"),
            images=data.get("images") or [],
            scoreWeight=data.get("scoreWeight", 1.0),
            ownerId=data.get("ownerId"),
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "VALIDATION_ERROR",
                "message": "Request validation failed",
                "details": [{"field": ".".join(str(item) for item in err["loc"]), "reason": err["msg"]} for err in exc.errors()],
            },
        ) from exc

    normalized_data = normalized.model_dump()
    normalized_data["hasLatex"] = patch.hasLatex if patch.hasLatex is not None else has_latex(normalized)
    return QuestionEntity(
        id=question.id,
        createdAt=question.createdAt,
        updatedAt=now_utc(),
        **normalized_data,
    )


def question_to_dict(question: QuestionEntity, include_answer: bool = True) -> dict[str, Any]:
    payload = question.model_dump(mode="json")
    if not include_answer:
        payload.pop("answer", None)
    return payload


def paper_to_dict(paper: PaperEntity) -> dict[str, Any]:
    return paper.model_dump(mode="json")


def envelope(data: Any, request: Request) -> dict[str, Any]:
    return {
        "success": True,
        "data": data,
        "meta": {"requestId": request.state.request_id},
    }


def error_envelope(code: str, message: str, request: Request, details: Any | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return {
        "success": False,
        "error": error,
        "meta": {"requestId": request.state.request_id},
    }


def get_question_or_404(question_id: int) -> QuestionEntity:
    question = QUESTIONS.get(question_id)
    if question is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "QUESTION_NOT_FOUND", "message": "Question not found"})
    return question


def get_paper_or_404(paper_id: int) -> PaperEntity:
    paper = PAPERS.get(paper_id)
    if paper is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "PAPER_NOT_FOUND", "message": "Paper not found"})
    return paper


def ensure_owner_exists(owner_id: int | None) -> None:
    if owner_id is None:
        return
    with SessionLocal() as session:
        if session.get(UserRow, owner_id) is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "VALIDATION_ERROR", "message": "ownerId must reference an existing user"},
            )


def normalize_question_owner(owner_id: int | None, current_user: UserEntity) -> int:
    requested_owner_id = owner_id if owner_id is not None else current_user.id
    if requested_owner_id != current_user.id and not has_permission(current_user, "users:manage"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": "Only administrators can assign questions to another user"},
        )
    ensure_owner_exists(requested_owner_id)
    return requested_owner_id


def ensure_question_owner_access(question: QuestionEntity, current_user: UserEntity) -> None:
    if question.ownerId in (None, current_user.id) or has_permission(current_user, "users:manage"):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"code": "FORBIDDEN", "message": "You can only modify questions you own"},
    )


def ensure_can_create_question(current_user: UserEntity) -> None:
    if current_user.role != UserRole.teacher:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"code": "FORBIDDEN", "message": "Teacher accounts cannot create questions"},
    )


def validate_question_payload(payload: QuestionBase) -> None:
    if payload.type in (QuestionType.choice, QuestionType.true_false) and not payload.options:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"code": "VALIDATION_ERROR", "message": f"{payload.type.value} questions require options"})
    if payload.type not in (QuestionType.choice, QuestionType.true_false) and payload.options is not None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"code": "VALIDATION_ERROR", "message": "options are only allowed for choice and true_false questions"})


def validate_unique_question_refs(items: list[QuestionRef], message_prefix: str) -> None:
    question_ids = [item.questionId for item in items]
    order_nos = [item.orderNo for item in items]
    if len(question_ids) != len(set(question_ids)):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"code": "VALIDATION_ERROR", "message": f"{message_prefix} must not contain duplicate question IDs"})
    if len(order_nos) != len(set(order_nos)):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"code": "VALIDATION_ERROR", "message": f"{message_prefix} must not contain duplicate order numbers"})


QUESTION_SORT_COLUMNS = {
    "id": QuestionRow.id,
    "createdAt": QuestionRow.created_at,
    "updatedAt": QuestionRow.updated_at,
    "subject": func.lower(QuestionRow.subject),
    "difficulty": QuestionRow.difficulty,
    "type": QuestionRow.type,
}


@dataclass(frozen=True)
class GenerationQuestionFeatures:
    difficulty: Difficulty
    question_type: QuestionType
    tags: frozenset[str]
    subject: str
    score_weight: float


def question_has_tag(tag: str):
    tag_values = func.jsonb_array_elements_text(QuestionRow.tags).table_valued("value").alias("tag_values")
    return select(1).select_from(tag_values).where(func.lower(tag_values.c.value) == tag).exists()


def query_questions_page(
    q: str | None = None,
    subject: str | None = None,
    difficulty: Difficulty | None = None,
    question_type: QuestionType | None = None,
    tags: str | None = None,
    has_latex_filter: bool | None = None,
    owner_id: int | None = None,
    sort_by: str | None = None,
    sort_order: SortOrder = SortOrder.desc,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    tag_filters = [item.strip().lower() for item in tags.split(",") if item.strip()] if tags else []
    keyword = q.lower().strip() if q else None

    statement = select(QuestionRow)
    if subject:
        statement = statement.where(QuestionRow.subject == subject)
    if difficulty:
        statement = statement.where(QuestionRow.difficulty == difficulty.value)
    if question_type:
        statement = statement.where(QuestionRow.type == question_type.value)
    if has_latex_filter is not None:
        statement = statement.where(QuestionRow.has_latex == has_latex_filter)
    if owner_id is not None:
        statement = statement.where(QuestionRow.owner_id == owner_id)
    if keyword:
        keyword_pattern = f"%{keyword}%"
        statement = statement.where(
            or_(
                func.lower(QuestionRow.text).like(keyword_pattern),
                func.lower(QuestionRow.subject).like(keyword_pattern),
                func.lower(QuestionRow.answer).like(keyword_pattern),
                func.lower(func.coalesce(QuestionRow.source, "")).like(keyword_pattern),
                func.lower(func.coalesce(sql_cast(QuestionRow.tags, String), "")).like(keyword_pattern),
                func.lower(func.coalesce(sql_cast(QuestionRow.options, String), "")).like(keyword_pattern),
            )
        )
    for tag in tag_filters:
        statement = statement.where(question_has_tag(tag))

    sort_column = QUESTION_SORT_COLUMNS.get(sort_by or "createdAt", QUESTION_SORT_COLUMNS["createdAt"])
    order_by = sort_column.desc() if sort_order == SortOrder.desc else sort_column.asc()
    id_order = QuestionRow.id.desc() if sort_order == SortOrder.desc else QuestionRow.id.asc()
    offset = (page - 1) * page_size

    with SessionLocal() as session:
        total = int(session.scalar(select(func.count()).select_from(statement.order_by(None).subquery())) or 0)
        rows = session.scalars(statement.order_by(order_by, id_order).offset(offset).limit(page_size)).all()

    return {
        "items": [question_row_to_entity(row) for row in rows],
        "pagination": {
            "page": page,
            "pageSize": page_size,
            "total": total,
            "totalPages": ceil(total / page_size) if total else 0,
        },
    }


def paper_with_questions(paper: PaperEntity, include_answer: bool = True) -> dict[str, Any]:
    normalized = paper_to_dict(paper)
    normalized["questions"] = sorted(normalized["questions"], key=lambda item: item["orderNo"])
    resolved_questions = []
    for item in normalized["questions"]:
        question = QUESTIONS.get(item["questionId"])
        if question is None:
            continue
        resolved_questions.append({
            **question_to_dict(question, include_answer=include_answer),
            "orderNo": item["orderNo"],
            "marks": item.get("marks"),
        })
    normalized["questions"] = resolved_questions
    return normalized


def build_export_questions(paper: PaperEntity, question_order: QuestionOrder, include_answer: bool) -> list[dict[str, Any]]:
    ordered_questions = paper_with_questions(paper, include_answer=include_answer)["questions"]
    if question_order == QuestionOrder.paper:
        return ordered_questions

    grouped: dict[QuestionType, list[dict[str, Any]]] = {QuestionType.choice: [], QuestionType.true_false: [], QuestionType.blank: [], QuestionType.short_answer: [], QuestionType.essay: []}
    for question in ordered_questions:
        grouped[QuestionType(question["type"])].append(question)
    flattened: list[dict[str, Any]] = []
    for qtype in (QuestionType.choice, QuestionType.true_false, QuestionType.blank, QuestionType.short_answer, QuestionType.essay):
        flattened.extend(grouped[qtype])
    return flattened


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
        question_index = ranked[index % len(ranked)][0]
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
    if payload.allocationMode == GenerationAllocationMode.question_count:
        return payload.questionCount or 10

    average_weight = sum(max(0.01, question.scoreWeight) for question in candidates) / len(candidates)
    estimated_count = max(1, round(payload.totalMarks / max(0.01, average_weight)))
    return max(1, min(estimated_count, payload.totalMarks, len(candidates), 100))


def default_difficulty_targets(question_count: int) -> dict[Difficulty, int]:
    easy = round(question_count * 0.30)
    medium = round(question_count * 0.50)
    hard = question_count - easy - medium
    return {
        Difficulty.easy: easy,
        Difficulty.medium: medium,
        Difficulty.hard: hard,
    }


def normalize_targets[T](targets: dict[T, int], question_count: int) -> dict[T, int]:
    total = sum(targets.values())
    if not targets or total == question_count:
        return targets

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


def build_generation_candidates(payload: PaperGenerateRequest) -> list[QuestionEntity]:
    statement = select(QuestionRow)
    if payload.subjectStrict:
        statement = statement.where(QuestionRow.subject == payload.subject)
    with SessionLocal() as session:
        rows = session.scalars(statement.order_by(QuestionRow.id)).all()
        candidates = [question_row_to_entity(row) for row in rows]
    required_count = payload.questionCount if payload.allocationMode == GenerationAllocationMode.question_count else 1
    if len(candidates) < (required_count or 1):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "INSUFFICIENT_QUESTIONS",
                "message": f"Need at least {required_count or 1} candidate questions, found {len(candidates)}.",
            },
        )
    required_tags = {tag.lower() for tag in payload.requiredTags}
    if required_tags:
        candidate_tags = {tag.lower() for question in candidates for tag in question.tags}
        missing_tags = sorted(required_tags - candidate_tags)
        if missing_tags:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "INSUFFICIENT_QUESTIONS",
                    "message": f"Candidate questions do not cover required tags: {', '.join(missing_tags)}.",
                    "details": {"missingTags": missing_tags},
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


def crossover_individual(parent_a: list[int], parent_b: list[int], candidate_ids: list[int], question_count: int, rng: random.Random) -> list[int]:
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


def generate_paper_with_genetic_algorithm(payload: PaperGenerateRequest) -> dict[str, Any]:
    candidates = build_generation_candidates(payload)
    question_by_id = {question.id: question for question in candidates}
    question_features = build_generation_features(candidates)
    candidate_ids = list(question_by_id)
    rng = random.Random(payload.algorithm.randomSeed)
    question_count = resolve_generation_question_count(payload, candidates)
    if len(candidates) < question_count:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "INSUFFICIENT_QUESTIONS",
                "message": f"Need at least {question_count} candidate questions, found {len(candidates)}.",
            },
        )
    difficulty_targets = normalize_targets(payload.difficultyTargets or default_difficulty_targets(question_count), question_count)
    type_targets = normalize_targets(payload.typeTargets, question_count)
    required_tags = {tag.lower() for tag in payload.requiredTags}
    optional_tags = {tag.lower() for tag in payload.optionalTags} - required_tags
    population_size = max(payload.algorithm.populationSize, payload.algorithm.elitismCount + payload.algorithm.tournamentSize)
    population_size = min(population_size, max(1, len(candidate_ids) * 8))
    target_score_weight = payload.totalMarks if payload.allocationMode == GenerationAllocationMode.total_score else None

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
                "allocationMode": payload.allocationMode.value,
                "scoreWeightActual": round(sum(max(0.01, question.scoreWeight) for question in selected_questions), 2),
                "marksActual": sum(marks),
                "difficultyTargets": {key.value: value for key, value in difficulty_targets.items()},
                "difficultyActual": dict(Counter(question.difficulty.value for question in selected_questions)),
                "typeTargets": {key.value: value for key, value in type_targets.items()},
                "typeActual": dict(Counter(question.type.value for question in selected_questions)),
                "requiredTags": sorted(required_tags),
                "coveredRequiredTags": sorted(required_tags & {tag.lower() for question in selected_questions for tag in question.tags}),
                "optionalTags": sorted(optional_tags),
                "coveredOptionalTags": sorted(optional_tags & {tag.lower() for question in selected_questions for tag in question.tags}),
                "algorithm": payload.algorithm.model_dump(),
                "generationsRun": 0,
            },
        }

    population = [rng.sample(candidate_ids, question_count) for _ in range(population_size)]
    best = population[0]
    best_score = float("-inf")
    generations_without_improvement = 0
    generations_run = 0

    def score(individual: list[int]) -> float:
        return individual_fitness(individual, question_features, difficulty_targets, type_targets, required_tags, optional_tags, target_score_weight)

    for generation in range(payload.algorithm.generations):
        generations_run = generation + 1
        ranked = sorted(((score(individual), individual) for individual in population), key=lambda item: item[0], reverse=True)
        if ranked[0][0] > best_score:
            best_score, best = ranked[0][0], ranked[0][1][:]
            generations_without_improvement = 0
        else:
            generations_without_improvement += 1
        if generations_without_improvement >= 30 and generation >= 50:
            break

        elite_count = min(payload.algorithm.elitismCount, len(ranked))
        next_population = [individual[:] for _, individual in ranked[:elite_count]]

        while len(next_population) < population_size:
            tournament = rng.sample(ranked, min(payload.algorithm.tournamentSize, len(ranked)))
            parent_a = max(tournament, key=lambda item: item[0])[1]
            tournament = rng.sample(ranked, min(payload.algorithm.tournamentSize, len(ranked)))
            parent_b = max(tournament, key=lambda item: item[0])[1]

            if rng.random() < payload.algorithm.crossoverRate:
                child = crossover_individual(parent_a, parent_b, candidate_ids, question_count, rng)
            else:
                child = parent_a[:]
            next_population.append(mutate_individual(child, candidate_ids, payload.algorithm.mutationRate, rng))

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
        "allocationMode": payload.allocationMode.value,
        "scoreWeightActual": round(sum(max(0.01, question.scoreWeight) for question in selected_questions), 2),
        "marksActual": sum(marks),
        "difficultyTargets": {key.value: value for key, value in difficulty_targets.items()},
        "difficultyActual": dict(Counter(question.difficulty.value for question in selected_questions)),
        "typeTargets": {key.value: value for key, value in type_targets.items()},
        "typeActual": dict(Counter(question.type.value for question in selected_questions)),
        "requiredTags": sorted(required_tags),
        "coveredRequiredTags": sorted(required_tags & {tag.lower() for question in selected_questions for tag in question.tags}),
        "optionalTags": sorted(optional_tags),
        "coveredOptionalTags": sorted(optional_tags & {tag.lower() for question in selected_questions for tag in question.tags}),
        "algorithm": payload.algorithm.model_dump(),
        "generationsRun": generations_run,
    }
    return {
        "paperQuestions": paper_questions,
        "selectedQuestions": selected_questions,
        "diagnostics": diagnostics,
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    if engine is None:
        raise RuntimeError("DATABASE_URL is required before starting the app.")
    if engine.url.get_backend_name() == "sqlite":
        raise RuntimeError("SQLite is not supported. Set DATABASE_URL to a PostgreSQL database.")
    # Optionally pre-warm Redis (best-effort)
    try:
        from redis_client import get_redis
        get_redis()
    except Exception:
        pass
    try:
        yield
    finally:
        engine.dispose()
        try:
            from redis_client import close_redis
            close_redis()
        except Exception:
            pass


app = create_app(lifespan=lifespan)



@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request.state.request_id = request.headers.get("x-request-id", str(uuid4()))
    response = await call_next(request)
    response.headers["x-request-id"] = request.state.request_id
    return response


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=error_envelope(
            "VALIDATION_ERROR",
            "Request validation failed",
            request,
            details=[{"field": ".".join(str(item) for item in error["loc"][1:]), "reason": error["msg"]} for error in exc.errors()],
        ),
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "INTERNAL_ERROR", "message": str(exc.detail)}
    return JSONResponse(
        status_code=exc.status_code,
        content=error_envelope(detail.get("code", "INTERNAL_ERROR"), detail.get("message", "An error occurred"), request, detail.get("details")),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=error_envelope("INTERNAL_ERROR", "Internal server error", request),
    )


@app.get("/")
async def root(request: Request):
    return envelope({"service": "TestPaper Backend", "version": "1.0.0"}, request)


@app.post("/api/v1/auth/login")
async def login(request: Request, response: Response, payload: LoginRequest):
    username = payload.username.strip().lower()
    with SessionLocal() as session:
        user_row = cast(UserRow | None, session.scalars(select(UserRow).where(UserRow.username == username)).first())
        if user_row is None or not user_row.is_active or not verify_password(payload.password, user_row.password_hash):
            raise auth_error("INVALID_CREDENTIALS", "Invalid username or password")

        token, auth_session = create_auth_session(session, user_row)
        set_auth_cookie(response, token, auth_session.expiresAt)
        return envelope(auth_session.model_dump(mode="json"), request)


@app.post("/api/v1/auth/register", status_code=status.HTTP_201_CREATED)
async def register(request: Request, response: Response, payload: RegisterRequest):
    with SessionLocal() as session:
        existing = session.scalars(select(UserRow).where(UserRow.username == payload.username)).first()
        if existing is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": "USER_ALREADY_EXISTS", "message": "Username already exists"})

        now = now_utc()
        user_row = UserRow(
            username=payload.username,
            display_name=payload.displayName,
            password_hash=password_hash(payload.password),
            role=UserRole.viewer.value,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        session.add(user_row)
        session.flush()

        token, auth_session = create_auth_session(session, user_row)
        set_auth_cookie(response, token, auth_session.expiresAt)
        return envelope(auth_session.model_dump(mode="json"), request)


@app.get("/api/v1/auth/me")
async def get_me(request: Request, current_user: UserEntity = Depends(get_current_user)):
    return envelope(current_user.model_dump(mode="json"), request)


@app.post("/api/v1/auth/refresh")
async def refresh_session(request: Request, response: Response):
    token, auth_session = refresh_auth_session(get_request_token(request))
    set_auth_cookie(response, token, auth_session.expiresAt)
    return envelope(auth_session.model_dump(mode="json"), request)


@app.post("/api/v1/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request):
    token = get_request_token(request)
    with SessionLocal() as session:
        token_row = session.get(AuthTokenRow, token) if token else None
        if token_row is not None:
            session.delete(token_row)
            session.commit()
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_auth_cookie(response)
    return response


@app.websocket("/api/v1/ws")
async def websocket_endpoint(websocket: WebSocket):
    try:
        current_user = get_user_from_token(get_websocket_token(websocket))
    except HTTPException:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await realtime.connect(websocket)
    try:
        await websocket.send_json(
            {
                "event": "auth.connected",
                "payload": {
                    "user": current_user.model_dump(mode="json"),
                    "serverTime": now_utc().isoformat(),
                },
            }
        )
        while True:
            raw_message = await websocket.receive_text()
            try:
                message = json.loads(raw_message)
            except json.JSONDecodeError:
                await websocket.send_json({"event": "error", "payload": {"message": "Invalid JSON message"}})
                continue

            if message.get("event") == "ping":
                await websocket.send_json({"event": "pong", "payload": {"serverTime": now_utc().isoformat()}})
    except WebSocketDisconnect:
        pass
    finally:
        realtime.disconnect(websocket)


@app.get("/api/v1/users")
async def list_users(request: Request, current_user: UserEntity = Depends(require_permission("users:manage"))):
    with SessionLocal() as session:
        rows = session.scalars(select(UserRow).order_by(UserRow.id)).all()
        return envelope([user_row_to_entity(row).model_dump(mode="json") for row in rows], request)


@app.post("/api/v1/users", status_code=status.HTTP_201_CREATED)
async def create_user(request: Request, payload: UserCreate, current_user: UserEntity = Depends(require_permission("users:manage"))):
    with SessionLocal() as session:
        existing = session.scalars(select(UserRow).where(UserRow.username == payload.username)).first()
        if existing is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": "USER_ALREADY_EXISTS", "message": "Username already exists"})

        now = now_utc()
        user_row = UserRow(
            username=payload.username,
            display_name=payload.displayName,
            password_hash=password_hash(payload.password),
            role=payload.role.value,
            is_active=payload.isActive,
            created_at=now,
            updated_at=now,
        )
        session.add(user_row)
        session.commit()
        session.refresh(user_row)
        return envelope(user_row_to_entity(user_row).model_dump(mode="json"), request)


@app.patch("/api/v1/users/{user_id}")
async def update_user(request: Request, user_id: int, payload: UserUpdate, current_user: UserEntity = Depends(require_permission("users:manage"))):
    with SessionLocal() as session:
        user_row = cast(UserRow | None, session.get(UserRow, user_id))
        if user_row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "USER_NOT_FOUND", "message": "User not found"})

        patch = payload.model_dump(exclude_unset=True)
        if "displayName" in patch:
            user_row.display_name = patch["displayName"]
        if "password" in patch:
            user_row.password_hash = password_hash(patch["password"])
        if "role" in patch and patch["role"] is not None:
            user_row.role = patch["role"].value if isinstance(patch["role"], UserRole) else str(patch["role"])
        if "isActive" in patch:
            user_row.is_active = bool(patch["isActive"])
        user_row.updated_at = now_utc()
        session.commit()
        session.refresh(user_row)
        return envelope(user_row_to_entity(user_row).model_dump(mode="json"), request)


@app.delete("/api/v1/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: int, current_user: UserEntity = Depends(require_permission("users:manage"))):
    if current_user.id == user_id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"code": "VALIDATION_ERROR", "message": "You cannot delete your own account"})
    with SessionLocal() as session:
        user_row = session.get(UserRow, user_id)
        if user_row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "USER_NOT_FOUND", "message": "User not found"})
        session.delete(user_row)
        session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/api/v1/meta/subjects")
async def list_subjects(request: Request, current_user: UserEntity = Depends(require_permission("questions:read"))):
    with SessionLocal() as session:
        subjects = session.scalars(select(QuestionRow.subject).distinct().order_by(QuestionRow.subject)).all()
    return envelope(list(subjects), request)


@app.get("/api/v1/meta/tags")
async def list_tags(request: Request, current_user: UserEntity = Depends(require_permission("questions:read"))):
    with SessionLocal() as session:
        tag_lists = session.scalars(select(QuestionRow.tags)).all()
    counter = Counter(str(tag) for tags in tag_lists for tag in (tags or []) if tag is not None)
    return envelope(sorted(counter.keys()), request)


@app.post("/api/v1/images/upload")
async def upload_image(
    request: Request,
    payload: ImageUploadPayload,
    current_user: UserEntity = Depends(require_permission("questions:write")),
):
    if payload.mimeType != "image/png":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "VALIDATION_ERROR", "message": "Only PNG images are supported"},
        )

    try:
        image_bytes = base64.b64decode(payload.data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "VALIDATION_ERROR", "message": "Image data must be valid base64"},
        ) from exc

    if len(image_bytes) > MAX_IMAGE_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"code": "PAYLOAD_TOO_LARGE", "message": "PNG image must be 30MB or smaller"},
        )

    if not image_bytes.startswith(PNG_SIGNATURE):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "VALIDATION_ERROR", "message": "Image data must be a PNG file"},
        )

    safe_name = f"{uuid4().hex}.png"
    data_url = f"data:{payload.mimeType};base64,{payload.data}"
    return envelope(
        ImageUploadResponse(url=data_url, filename=safe_name, mimeType=payload.mimeType).model_dump(mode="json"),
        request,
    )


@app.get("/api/v1/questions")
async def list_questions(
    request: Request,
    q: str | None = None,
    subject: str | None = None,
    difficulty: Difficulty | None = None,
    type: QuestionType | None = None,
    tags: str | None = None,
    hasLatex: bool | None = None,
    ownerId: int | None = None,
    includeAnswer: bool = True,
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=20, ge=1, le=100),
    sortBy: str | None = None,
    sortOrder: SortOrder = SortOrder.desc,
    current_user: UserEntity = Depends(require_permission("questions:read")),
):
    page_data = query_questions_page(
        q=q,
        subject=subject,
        difficulty=difficulty,
        question_type=type,
        tags=tags,
        has_latex_filter=hasLatex,
        owner_id=ownerId,
        sort_by=sortBy,
        sort_order=sortOrder,
        page=page,
        page_size=pageSize,
    )
    can_read_answers = has_permission(current_user, "answers:read")
    page_data["items"] = [question_to_dict(item, include_answer=includeAnswer and can_read_answers) for item in page_data["items"]]
    return envelope(page_data, request)


@app.get("/api/v1/questions/mine")
async def list_my_questions(
    request: Request,
    q: str | None = None,
    subject: str | None = None,
    difficulty: Difficulty | None = None,
    type: QuestionType | None = None,
    tags: str | None = None,
    hasLatex: bool | None = None,
    includeAnswer: bool = True,
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=20, ge=1, le=100),
    sortBy: str | None = None,
    sortOrder: SortOrder = SortOrder.desc,
    current_user: UserEntity = Depends(require_permission("questions:read")),
):
    page_data = query_questions_page(
        q=q,
        subject=subject,
        difficulty=difficulty,
        question_type=type,
        tags=tags,
        has_latex_filter=hasLatex,
        owner_id=current_user.id,
        sort_by=sortBy,
        sort_order=sortOrder,
        page=page,
        page_size=pageSize,
    )
    can_read_answers = has_permission(current_user, "answers:read")
    page_data["items"] = [question_to_dict(item, include_answer=includeAnswer and can_read_answers) for item in page_data["items"]]
    return envelope(page_data, request)


@app.get("/api/v1/questions/{question_id}")
async def get_question(
    request: Request,
    question_id: int,
    includeAnswer: bool = True,
    current_user: UserEntity = Depends(require_permission("questions:read")),
):
    question = get_question_or_404(question_id)
    return envelope(question_to_dict(question, include_answer=includeAnswer and has_permission(current_user, "answers:read")), request)


@app.post("/api/v1/questions", status_code=status.HTTP_201_CREATED)
async def create_question(request: Request, payload: QuestionCreate, current_user: UserEntity = Depends(require_permission("questions:write"))):
    ensure_can_create_question(current_user)
    validate_question_payload(payload)
    payload.ownerId = normalize_question_owner(payload.ownerId, current_user)
    question = QUESTIONS.create(normalize_question_payload(payload, question_id=0))
    await realtime.broadcast("question.created", {"question": question_to_dict(question), "actorId": current_user.id})
    return envelope(question_to_dict(question), request)


@app.patch("/api/v1/questions/{question_id}")
async def update_question(
    request: Request,
    question_id: int,
    payload: QuestionUpdate,
    current_user: UserEntity = Depends(require_permission("questions:write")),
):
    question = get_question_or_404(question_id)
    ensure_question_owner_access(question, current_user)
    if "ownerId" in payload.model_fields_set and payload.ownerId is None and not has_permission(current_user, "users:manage"):
        payload.ownerId = current_user.id
    elif payload.ownerId is not None:
        payload.ownerId = normalize_question_owner(payload.ownerId, current_user)
    updated = apply_question_update(question, payload)
    QUESTIONS[question_id] = updated
    await realtime.broadcast("question.updated", {"question": question_to_dict(updated), "actorId": current_user.id})
    return envelope(question_to_dict(updated), request)


@app.delete("/api/v1/questions/{question_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_question(question_id: int, current_user: UserEntity = Depends(require_permission("questions:delete"))):
    question = get_question_or_404(question_id)
    ensure_question_owner_access(question, current_user)
    del QUESTIONS[question_id]
    await realtime.broadcast("question.deleted", {"questionId": question_id, "actorId": current_user.id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/api/v1/papers", status_code=status.HTTP_201_CREATED)
async def create_paper(request: Request, payload: PaperCreate, current_user: UserEntity = Depends(require_permission("papers:write"))):
    validate_unique_question_refs(payload.questions, "questions")
    for item in payload.questions:
        get_question_or_404(item.questionId)
    paper = PaperEntity(
        id=0,
        title=payload.title,
        subject=payload.subject,
        duration=payload.duration,
        totalMarks=payload.totalMarks,
        questions=[PaperQuestion(**item.model_dump()) for item in sorted(payload.questions, key=lambda item: item.orderNo)],
        status=PaperStatus.draft,
        createdAt=now_utc(),
        updatedAt=now_utc(),
    )
    paper = PAPERS.create(paper)
    await realtime.broadcast("paper.created", {"paper": paper_to_dict(paper), "actorId": current_user.id})
    return envelope(paper_with_questions(paper), request)


@app.post("/api/v1/papers/generate", status_code=status.HTTP_201_CREATED)
async def generate_paper(request: Request, payload: PaperGenerateRequest, current_user: UserEntity = Depends(require_permission("papers:write"))):
    generated = generate_paper_with_genetic_algorithm(payload)
    paper = PaperEntity(
        id=0,
        title=payload.title,
        subject=payload.subject,
        duration=payload.duration,
        totalMarks=payload.totalMarks,
        questions=generated["paperQuestions"],
        status=PaperStatus.draft,
        createdAt=now_utc(),
        updatedAt=now_utc(),
    )
    paper = PAPERS.create(paper)
    paper_payload = paper_with_questions(paper)
    await realtime.broadcast("paper.created", {"paper": paper_to_dict(paper), "actorId": current_user.id})
    return envelope(
        {
            "paper": paper_payload,
            "diagnostics": generated["diagnostics"],
        },
        request,
    )


@app.get("/api/v1/papers/{paper_id}")
async def get_paper(
    request: Request,
    paper_id: int,
    expand: str | None = None,
    includeAnswer: bool = True,
    current_user: UserEntity = Depends(require_permission("papers:read")),
):
    paper = get_paper_or_404(paper_id)
    if expand == "questions":
        return envelope(paper_with_questions(paper, include_answer=includeAnswer and has_permission(current_user, "answers:read")), request)
    return envelope(paper_to_dict(paper), request)


@app.patch("/api/v1/papers/{paper_id}")
async def update_paper(
    request: Request,
    paper_id: int,
    payload: PaperUpdate,
    current_user: UserEntity = Depends(require_permission("papers:write")),
):
    paper = get_paper_or_404(paper_id)
    data = paper.model_dump()
    patch = payload.model_dump(exclude_unset=True)
    data.update(patch)
    data["updatedAt"] = now_utc()
    updated = PaperEntity(**data)
    PAPERS[paper_id] = updated
    await realtime.broadcast("paper.updated", {"paper": paper_to_dict(updated), "actorId": current_user.id})
    return envelope(paper_to_dict(updated), request)


@app.post("/api/v1/papers/{paper_id}/questions")
async def add_paper_questions(
    request: Request,
    paper_id: int,
    payload: list[QuestionRef],
    current_user: UserEntity = Depends(require_permission("papers:write")),
):
    paper = get_paper_or_404(paper_id)
    validate_unique_question_refs(payload, "questions")
    existing_ids = {item.questionId for item in paper.questions}
    existing_orders = {item.orderNo for item in paper.questions}
    additions = []
    for item in payload:
        get_question_or_404(item.questionId)
        if item.questionId in existing_ids:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": "QUESTION_ALREADY_IN_PAPER", "message": "Question already exists in paper"})
        if item.orderNo in existing_orders:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"code": "VALIDATION_ERROR", "message": "orderNo already exists in paper"})
        additions.append(PaperQuestion(**item.model_dump()))
    paper.questions.extend(additions)
    paper.questions = sorted(paper.questions, key=lambda item: item.orderNo)
    paper.updatedAt = now_utc()
    PAPERS[paper_id] = paper
    await realtime.broadcast("paper.questions.added", {"paper": paper_to_dict(paper), "actorId": current_user.id})
    return envelope(paper_with_questions(paper), request)


@app.delete("/api/v1/papers/{paper_id}/questions/{question_id}")
async def remove_paper_question(
    request: Request,
    paper_id: int,
    question_id: int,
    current_user: UserEntity = Depends(require_permission("papers:write")),
):
    paper = get_paper_or_404(paper_id)
    before = len(paper.questions)
    paper.questions = [item for item in paper.questions if item.questionId != question_id]
    if len(paper.questions) == before:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "QUESTION_NOT_FOUND", "message": "Question not found in paper"})
    paper.updatedAt = now_utc()
    PAPERS[paper_id] = paper
    await realtime.broadcast("paper.question.removed", {"paper": paper_to_dict(paper), "questionId": question_id, "actorId": current_user.id})
    return envelope(paper_with_questions(paper), request)


@app.put("/api/v1/papers/{paper_id}/questions/order")
async def reorder_paper_questions(
    request: Request,
    paper_id: int,
    payload: QuestionOrderUpdate,
    current_user: UserEntity = Depends(require_permission("papers:write")),
):
    paper = get_paper_or_404(paper_id)
    validate_unique_question_refs([QuestionRef(questionId=item.questionId, orderNo=item.orderNo) for item in payload.orders], "orders")
    order_map = {item.questionId: item.orderNo for item in payload.orders}
    existing_ids = {item.questionId for item in paper.questions}
    if set(order_map) != existing_ids:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"code": "VALIDATION_ERROR", "message": "orders must include every question in the paper"})
    paper.questions = [PaperQuestion(questionId=item.questionId, orderNo=order_map[item.questionId], marks=item.marks) for item in paper.questions]
    paper.questions = sorted(paper.questions, key=lambda item: item.orderNo)
    paper.updatedAt = now_utc()
    PAPERS[paper_id] = paper
    await realtime.broadcast("paper.questions.reordered", {"paper": paper_to_dict(paper), "actorId": current_user.id})
    return envelope(paper_with_questions(paper), request)


@app.post("/api/v1/papers/{paper_id}/export-preview")
async def export_preview(
    request: Request,
    paper_id: int,
    payload: ExportPreviewRequest,
    current_user: UserEntity = Depends(require_permission("papers:read")),
):
    paper = get_paper_or_404(paper_id)
    questions = build_export_questions(paper, payload.questionOrder, payload.includeAnswer and has_permission(current_user, "answers:read"))
    return envelope(
        {
            "paper": paper_to_dict(paper),
            "questions": questions,
            "renderHint": payload.model_dump(),
        },
        request,
    )


@app.get("/api/v1/papers/{paper_id}/download")
async def download_paper(
    paper_id: int,
    format: str = Query(default="docx", pattern="^docx$"),
    questionOrder: QuestionOrder = QuestionOrder.paper,
    includeAnswer: bool = True,
    current_user: UserEntity = Depends(require_permission("papers:read")),
):
    paper = get_paper_or_404(paper_id)
    questions = build_export_questions(paper, questionOrder, includeAnswer and has_permission(current_user, "answers:read"))
    file_bytes = build_paper_docx(paper, questions, include_answer=includeAnswer and has_permission(current_user, "answers:read"))
    filename = docx_filename(paper.title)
    ascii_filename = docx_filename(paper.title.encode("ascii", "ignore").decode("ascii") or "examination-paper")

    return Response(
        content=file_bytes,
        media_type=DOCX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f"attachment; filename=\"{ascii_filename}\"; filename*=UTF-8''{quote(filename)}",
            "X-Export-Format": format,
        },
    )


# ---------------------------------------------------------------------------
# Health checks
# ---------------------------------------------------------------------------
@app.get("/api/v1/health/postgres")
async def postgres_health(request: Request):
    try:
        if engine is None:
            raise RuntimeError("DATABASE_URL is not configured.")
        start = perf_counter()
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            version = connection.execute(text("SELECT version()")).scalar_one_or_none()
        latency_ms = round((perf_counter() - start) * 1000, 2)
        return envelope(
            {"status": "connected", "postgresVersion": version, "latencyMs": latency_ms},
            request,
        )
    except Exception as exc:
        return envelope(
            {"status": "disconnected", "error": str(exc)},
            request,
        )


@app.get("/api/v1/health/redis")
async def redis_health(request: Request):
    try:
        from redis_client import get_redis
        client = get_redis()
        start = perf_counter()
        client.ping()
        latency_ms = round((perf_counter() - start) * 1000, 2)
        info = cast(dict[str, Any], client.info(section="server"))
        return envelope(
            {"status": "connected", "redisVersion": info.get("redis_version"), "latencyMs": latency_ms},
            request,
        )
    except Exception as exc:
        return envelope(
            {"status": "disconnected", "error": str(exc)},
            request,
        )


# ---------------------------------------------------------------------------
# Celery async task endpoints
# ---------------------------------------------------------------------------
@app.post("/api/v1/tasks/ping")
async def task_ping(request: Request, current_user: UserEntity = Depends(require_permission("questions:read"))):
    """Dispatch a Celery ping task and return its task ID for polling."""
    from celery_app import celery
    result = celery.send_task("ping")
    return envelope({"taskId": result.id, "status": "dispatched"}, request)


@app.get("/api/v1/tasks/{task_id}")
async def task_status(request: Request, task_id: str, current_user: UserEntity = Depends(require_permission("questions:read"))):
    """Poll the status/result of any Celery task by ID."""
    from celery.result import AsyncResult

    from celery_app import celery
    result = AsyncResult(task_id, app=celery)

    response_data: dict[str, Any] = {
        "taskId": task_id,
        "status": result.state,
    }
    if result.state == "SUCCESS":
        response_data["result"] = result.result
    elif result.state == "FAILURE":
        response_data["error"] = str(result.info) if result.info else "Unknown error"
    elif result.state == "PROGRESS":
        response_data["progress"] = result.info if result.info else None
    elif result.state == "REVOKED":
        response_data["message"] = "Task was revoked"

    return envelope(response_data, request)


@app.post("/api/v1/tasks/export-paper/{paper_id}")
async def task_export_paper(
    request: Request,
    paper_id: int,
    question_order: str = Query(default="paper", pattern="^(paper|categorized)$"),
    include_answer: bool = Query(default=True),
    export_format: str = Query(default="json", alias="format", pattern="^(json|csv|txt)$"),
    current_user: UserEntity = Depends(require_permission("papers:read")),
):
    """Dispatch an asynchronous paper export. Returns a task ID for polling."""

    from celery_app import celery

    result = celery.send_task(
        "export_paper",
        args=[paper_id],
        kwargs={
            "question_order": question_order,
            "include_answer": include_answer and has_permission(current_user, "answers:read"),
            "format": export_format,
        },
    )
    return envelope(
        {"taskId": result.id, "status": "dispatched", "paperId": paper_id},
        request,
    )


@app.post("/api/v1/tasks/validate-questions")
async def task_validate_all_questions(
    request: Request,
    current_user: UserEntity = Depends(require_permission("questions:read")),
):
    """Dispatch an async validation of all questions."""
    from celery_app import celery

    result = celery.send_task("validate_all_questions")
    return envelope({"taskId": result.id, "status": "dispatched"}, request)


@app.post("/api/v1/tasks/validate-question/{question_id}")
async def task_validate_question(
    request: Request,
    question_id: int,
    current_user: UserEntity = Depends(require_permission("questions:read")),
):
    """Dispatch an async validation of a single question."""
    from celery_app import celery

    result = celery.send_task("validate_question", args=[question_id])
    return envelope({"taskId": result.id, "status": "dispatched", "questionId": question_id}, request)


@app.post("/api/v1/tasks/cleanup-expired-sessions")
async def task_cleanup_expired_sessions(
    request: Request,
    current_user: UserEntity = Depends(require_permission("users:manage")),
):
    """Dispatch an async cleanup of expired auth tokens."""
    from celery_app import celery

    result = celery.send_task("cleanup_expired_sessions")
    return envelope({"taskId": result.id, "status": "dispatched"}, request)


@app.get("/api/v1/tasks/stats/questions")
async def task_compute_question_stats(
    request: Request,
    current_user: UserEntity = Depends(require_permission("questions:read")),
):
    """Dispatch async question statistics computation."""
    from celery_app import celery

    result = celery.send_task("compute_question_stats")
    return envelope({"taskId": result.id, "status": "dispatched"}, request)

