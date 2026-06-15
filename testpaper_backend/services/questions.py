from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from math import ceil
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy import ARRAY, String, func, or_, select, type_coerce
from sqlalchemy import cast as sql_cast

from testpaper_backend.db import (
    QuestionCorrectionRow,
    QuestionRevisionRow,
    QuestionRow,
    SessionLocal,
    UserRow,
)
from testpaper_backend.repositories import QUESTIONS, has_latex, question_row_to_entity
from testpaper_backend.schemas import (
    CorrectionStatus,
    Difficulty,
    QuestionBase,
    QuestionCorrectionCreate,
    QuestionCorrectionEntity,
    QuestionCreate,
    QuestionEntity,
    QuestionRevisionEntity,
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


def apply_question_update(
    question: QuestionEntity, patch: QuestionUpdate, current_user_id: int
) -> tuple[QuestionEntity, QuestionRevisionRow | None]:
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

    data["tags"] = [tag.strip().lower() for tag in (data.get("tags") or []) if tag and tag.strip()]
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
    updated = QuestionEntity(
        id=question.id,
        createdAt=question.createdAt,
        updatedAt=now_utc(),
        **normalized_data,
    )

    change_summary = generate_change_summary({k: v for k, v in patch_data.items() if k not in ("hasLatex", "ownerId")})
    if change_summary:
        revision = QuestionRevisionRow(
            question_id=question.id,
            user_id=current_user_id,
            patch=patch_data,
            change_summary=change_summary,
            created_at=now_utc(),
        )
        return updated, revision
    return updated, None


def question_to_dict(question: QuestionEntity, include_answer: bool = True) -> dict[str, Any]:
    payload = question.model_dump(mode="json")
    if not include_answer:
        payload["answer"] = "" if question.type != QuestionType.multiple_choice else []
    return payload


def get_question_or_404(question_public_id: str) -> QuestionEntity:
    question = QUESTIONS.get_by_public_id(question_public_id)
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
        subject_list = [s.strip() for s in subjects.split(",") if s.strip()]
        if subject_list:
            statement = statement.where(QuestionRow.subjects.op("?|")(type_coerce(subject_list, ARRAY(String))))
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
    if tag_filters:
        statement = statement.where(QuestionRow.tags.op("?|")(type_coerce(tag_filters, ARRAY(String))))

    sort_column = QUESTION_SORT_COLUMNS.get(sort_by or "createdAt", QUESTION_SORT_COLUMNS["createdAt"])
    order_by = sort_column.desc() if sort_order == SortOrder.desc else sort_column.asc()
    id_order = QuestionRow.id.desc() if sort_order == SortOrder.desc else QuestionRow.id.asc()
    offset = (page - 1) * page_size

    with SessionLocal() as session:
        count_stmt = select(func.count()).select_from(QuestionRow)
        if statement.whereclause is not None:
            count_stmt = count_stmt.where(statement.whereclause)
        total = int(session.scalar(count_stmt) or 0)
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


FIELD_DISPLAY_NAMES: dict[str, str] = {
    "type": "type",
    "subjects": "subjects",
    "difficulty": "difficulty",
    "tags": "tags",
    "text": "text",
    "options": "options",
    "answer": "answer",
    "source": "source",
    "essayBlankSpace": "essay blank space",
    "images": "images",
    "scoreWeight": "score weight",
}


def generate_change_summary(patch: dict[str, Any]) -> str:
    updated_fields = [FIELD_DISPLAY_NAMES.get(key, key) for key in patch if key in FIELD_DISPLAY_NAMES]
    if not updated_fields:
        return ""
    return "Updated " + ", ".join(updated_fields)


def correction_row_to_entity(row: QuestionCorrectionRow) -> QuestionCorrectionEntity:
    return QuestionCorrectionEntity(
        id=row.id,
        questionId=row.question_id,
        userId=row.user_id,
        category=row.category,
        message=row.message,
        status=row.status,
        createdAt=row.created_at,
        updatedAt=row.updated_at,
    )


def revision_row_to_entity(row: QuestionRevisionRow) -> QuestionRevisionEntity:
    return QuestionRevisionEntity(
        id=row.id,
        questionId=row.question_id,
        userId=row.user_id,
        patch=row.patch,
        changeSummary=row.change_summary,
        createdAt=row.created_at,
    )


def create_correction(question_id: int, user_id: int, payload: QuestionCorrectionCreate) -> QuestionCorrectionEntity:
    now = now_utc()
    row = QuestionCorrectionRow(
        question_id=question_id,
        user_id=user_id,
        category=payload.category.value,
        message=payload.message.strip(),
        status=CorrectionStatus.open.value,
        created_at=now,
        updated_at=now,
    )
    with SessionLocal() as session:
        session.add(row)
        session.commit()
        session.refresh(row)
        return correction_row_to_entity(row)


def list_corrections(question_id: int) -> list[QuestionCorrectionEntity]:
    with SessionLocal() as session:
        rows = session.scalars(
            select(QuestionCorrectionRow)
            .where(QuestionCorrectionRow.question_id == question_id)
            .order_by(QuestionCorrectionRow.created_at.desc())
        ).all()
        return [correction_row_to_entity(row) for row in rows]


def update_correction_status(correction_id: int, status: CorrectionStatus) -> QuestionCorrectionEntity:
    with SessionLocal() as session:
        row = session.get(QuestionCorrectionRow, correction_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "CORRECTION_NOT_FOUND", "message": "Correction not found"},
            )
        row.status = status.value
        row.updated_at = now_utc()
        session.commit()
        session.refresh(row)
        return correction_row_to_entity(row)


def list_revisions(question_id: int) -> list[QuestionRevisionEntity]:
    with SessionLocal() as session:
        rows = session.scalars(
            select(QuestionRevisionRow)
            .where(QuestionRevisionRow.question_id == question_id)
            .order_by(QuestionRevisionRow.created_at.desc())
        ).all()
        return [revision_row_to_entity(row) for row in rows]


def delete_revision(revision_id: int, question_id: int) -> None:
    with SessionLocal() as session:
        row = session.get(QuestionRevisionRow, revision_id)
        if row is None or row.question_id != question_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "REVISION_NOT_FOUND", "message": "Revision not found for this question"},
            )
        session.delete(row)
        session.commit()


def delete_correction_entry(correction_id: int, question_id: int) -> None:
    with SessionLocal() as session:
        row = session.get(QuestionCorrectionRow, correction_id)
        if row is None or row.question_id != question_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "CORRECTION_NOT_FOUND", "message": "Correction not found for this question"},
            )
        session.delete(row)
        session.commit()
