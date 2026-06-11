from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from math import ceil
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy import String, func, or_, select
from sqlalchemy import cast as sql_cast

from testpaper_backend.db import QuestionRow, SessionLocal, UserRow
from testpaper_backend.repositories import QUESTIONS, has_latex, question_row_to_entity
from testpaper_backend.schemas import (
    Difficulty,
    QuestionBase,
    QuestionCreate,
    QuestionEntity,
    QuestionType,
    QuestionUpdate,
    SortOrder,
    UserEntity,
)
from testpaper_backend.security import has_permission
from testpaper_backend.time_utils import now_utc

QUESTION_SORT_COLUMNS = {
    "id": QuestionRow.id,
    "createdAt": QuestionRow.created_at,
    "updatedAt": QuestionRow.updated_at,
    "subjects": func.lower(func.coalesce(QuestionRow.subjects[1].as_string(), "")),
    "difficulty": QuestionRow.difficulty,
    "type": QuestionRow.type,
}


def normalize_question_payload(payload: QuestionBase, question_id: int, created_at: datetime | None = None) -> QuestionEntity:
    normalized = deepcopy(payload.model_dump())
    normalized["hasLatex"] = payload.hasLatex if payload.hasLatex is not None else has_latex(payload)
    normalized["publicId"] = str(uuid4())
    return QuestionEntity(
        id=question_id,
        createdAt=created_at or now_utc(),
        updatedAt=now_utc(),
        **normalized,
    )


def apply_question_update(question: QuestionEntity, patch: QuestionUpdate) -> QuestionEntity:
    data = question.model_dump()
    patch_data = patch.model_dump(exclude_unset=True)
    data.update(patch_data)

    option_types = (QuestionType.single_choice, QuestionType.multiple_choice, QuestionType.true_false)
    if (
        (patch_data.get("type") and patch_data["type"] in option_types)
        or data.get("type") in option_types
    ):
        options = data.get("options") or []
        data["options"] = [option.strip() for option in options if option and option.strip()]
    elif data.get("type") not in option_types:
        data["options"] = None

    if data.get("type") == QuestionType.essay:
        if data.get("essayBlankSpace") is None:
            data["essayBlankSpace"] = {"lines": 6, "lineHeight": 28}
    else:
        data["essayBlankSpace"] = None

    data["tags"] = [tag.strip() for tag in (data.get("tags") or []) if tag and tag.strip()]
    data["subjects"] = [s.strip() for s in (data.get("subjects") or []) if s and s.strip()]
    if data.get("source") is not None:
        data["source"] = data["source"].strip() or None

    try:
        normalized = QuestionCreate(
            type=data["type"],
            subjects=data.get("subjects") or [],
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
    normalized_data["publicId"] = question.publicId
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


def get_question_or_404(question_id: int) -> QuestionEntity:
    question = QUESTIONS.get(question_id)
    if question is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "QUESTION_NOT_FOUND", "message": "Question not found"})
    return question


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


def validate_question_payload(payload: QuestionBase) -> None:
    option_types = (QuestionType.single_choice, QuestionType.multiple_choice, QuestionType.true_false)
    if payload.type in option_types and not payload.options:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "VALIDATION_ERROR", "message": f"{payload.type.value} questions require options"},
        )
    if payload.type not in option_types and payload.options is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "VALIDATION_ERROR", "message": "options are only allowed for choice and true_false questions"},
        )


def question_has_tag(tag: str):
    tag_values = func.jsonb_array_elements_text(QuestionRow.tags).table_valued("value").alias("tag_values")
    return select(1).select_from(tag_values).where(func.lower(tag_values.c.value) == tag).exists()


def query_questions_page(
    q: str | None = None,
    subjects: str | None = None,
    difficulty: Difficulty | None = None,
    question_type: QuestionType | None = None,
    tags: str | None = None,
    has_latex_filter: bool | None = None,
    owner_id: int | None = None,
    sort_by: str | None = None,
    sort_order: SortOrder = SortOrder.desc,
    page: int = 1,
    page_size: int = 20,
    search_answers: bool = False,
) -> dict[str, Any]:
    tag_filters = [item.strip().lower() for item in tags.split(",") if item.strip()] if tags else []
    keyword = q.lower().strip() if q else None

    statement = select(QuestionRow)
    if subjects:
        subject_list = [s.strip().lower() for s in subjects.split(",") if s.strip()]
        if subject_list:
            subject_conditions = [
                func.lower(func.coalesce(sql_cast(QuestionRow.subjects, String), "")).contains(s)
                for s in subject_list
            ]
            statement = statement.where(or_(*subject_conditions))
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
        search_conditions = [
            func.lower(QuestionRow.text).like(keyword_pattern),
            func.lower(func.coalesce(sql_cast(QuestionRow.subjects, String), "")).like(keyword_pattern),
            func.lower(func.coalesce(QuestionRow.source, "")).like(keyword_pattern),
            func.lower(func.coalesce(sql_cast(QuestionRow.tags, String), "")).like(keyword_pattern),
            func.lower(func.coalesce(sql_cast(QuestionRow.options, String), "")).like(keyword_pattern),
        ]
        if search_answers:
            search_conditions.append(func.lower(sql_cast(QuestionRow.answer, String)).like(keyword_pattern))
        statement = statement.where(or_(*search_conditions))
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
