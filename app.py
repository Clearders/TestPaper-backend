from __future__ import annotations

import secrets
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from math import ceil
from typing import Any, cast
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app_factory import create_app
from db import AuthTokenRow, PaperRow, QuestionRow, SessionLocal, UserRow, engine
from repositories import PAPERS, QUESTIONS, has_latex
from schemas import (
    AuthSession,
    Difficulty,
    EssayBlankSpace,
    ExportPreviewRequest,
    ImageUploadPayload,
    ImageUploadResponse,
    LoginRequest,
    PaperCreate,
    PaperEntity,
    PaperQuestion,
    PaperStatus,
    PaperUpdate,
    Permission,
    QuestionBase,
    QuestionCreate,
    QuestionEntity,
    QuestionImage,
    QuestionOrder,
    QuestionOrderUpdate,
    QuestionRef,
    QuestionType,
    RegisterRequest,
    QuestionUpdate,
    SortOrder,
    UserCreate,
    UserEntity,
    UserRole,
    UserUpdate,
)
from security import auth_error, get_current_user, has_permission, password_hash, require_permission, user_row_to_entity, verify_password
from time_utils import now_utc


def normalize_question_payload(payload: QuestionBase, question_id: int, created_at: datetime | None = None) -> QuestionEntity:
    normalized = deepcopy(payload.model_dump())
    normalized["hasLatex"] = payload.hasLatex if payload.hasLatex is not None else has_latex(payload)
    return QuestionEntity(
        id=question_id,
        createdAt=created_at or now_utc(),
        updatedAt=now_utc(),
        **normalized,
    )


def create_auth_session(session: Session, user_row: UserRow) -> AuthSession:
    now = now_utc()
    session.query(AuthTokenRow).filter(AuthTokenRow.expires_at <= now).delete(synchronize_session=False)
    token = secrets.token_urlsafe(48)
    expires_at = now + timedelta(hours=12)
    session.add(AuthTokenRow(token=token, user_id=user_row.id, created_at=now, expires_at=expires_at))
    session.commit()
    session.refresh(user_row)
    return AuthSession(token=token, expiresAt=expires_at, user=user_row_to_entity(user_row))


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


def sort_questions(items: list[QuestionEntity], sort_by: str | None, sort_order: SortOrder) -> list[QuestionEntity]:
    key_map = {
        "id": lambda item: item.id,
        "createdAt": lambda item: item.createdAt,
        "updatedAt": lambda item: item.updatedAt,
        "subject": lambda item: item.subject.lower(),
        "difficulty": lambda item: item.difficulty.value,
        "type": lambda item: item.type.value,
    }
    primary_key = key_map.get(sort_by or "createdAt", key_map["createdAt"])
    secondary_key = key_map["id"]
    reverse = sort_order == SortOrder.desc
    return sorted(items, key=lambda item: (primary_key(item), secondary_key(item)), reverse=reverse)


def filter_questions(
    q: str | None = None,
    subject: str | None = None,
    difficulty: Difficulty | None = None,
    question_type: QuestionType | None = None,
    tags: str | None = None,
    has_latex_filter: bool | None = None,
    owner_id: int | None = None,
) -> list[QuestionEntity]:
    tag_set = {item.strip().lower() for item in tags.split(",") if item.strip()} if tags else set()
    keyword = q.lower().strip() if q else None
    results = []
    for question in QUESTIONS.values():
        question_tags = [str(tag) for tag in (question.tags or []) if tag is not None]
        question_options = [str(option) for option in (question.options or []) if option is not None]
        if subject and question.subject != subject:
            continue
        if difficulty and question.difficulty != difficulty:
            continue
        if question_type and question.type != question_type:
            continue
        if has_latex_filter is not None and question.hasLatex != has_latex_filter:
            continue
        if owner_id is not None and question.ownerId != owner_id:
            continue
        if tag_set and not tag_set.issubset({tag.lower() for tag in question_tags}):
            continue
        if keyword:
            haystack = f"{question.text} {question.subject} {question.answer} {' '.join(question_tags)} {' '.join(question_options)}".lower()
            if keyword not in haystack:
                continue
        results.append(question)
    return results


def paginated(items: list[Any], page: int, page_size: int) -> dict[str, Any]:
    total = len(items)
    start = (page - 1) * page_size
    end = start + page_size
    return {
        "items": items[start:end],
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


NEXT_QUESTION_ID = 1
NEXT_PAPER_ID = 1


def refresh_next_ids() -> None:
    global NEXT_QUESTION_ID, NEXT_PAPER_ID
    if engine is None:
        raise RuntimeError("DATABASE_URL is required before starting the app.")
    with SessionLocal() as session:
        next_question_id = session.scalar(select(func.max(QuestionRow.id))) or 0
        next_paper_id = session.scalar(select(func.max(PaperRow.id))) or 0
    NEXT_QUESTION_ID = next_question_id + 1
    NEXT_PAPER_ID = next_paper_id + 1


@asynccontextmanager
async def lifespan(app: FastAPI):
    if engine is None:
        raise RuntimeError("DATABASE_URL is required before starting the app.")
    if engine.url.get_backend_name() == "sqlite":
        raise RuntimeError("SQLite is not supported. Set DATABASE_URL to a PostgreSQL database.")
    refresh_next_ids()
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
async def login(request: Request, payload: LoginRequest):
    username = payload.username.strip().lower()
    with SessionLocal() as session:
        user_row = cast(UserRow | None, session.scalars(select(UserRow).where(UserRow.username == username)).first())
        if user_row is None or not user_row.is_active or not verify_password(payload.password, user_row.password_hash):
            raise auth_error("INVALID_CREDENTIALS", "Invalid username or password")

        auth_session = create_auth_session(session, user_row)
        return envelope(auth_session.model_dump(mode="json"), request)


@app.post("/api/v1/auth/register", status_code=status.HTTP_201_CREATED)
async def register(request: Request, payload: RegisterRequest):
    with SessionLocal() as session:
        existing = session.scalars(select(UserRow).where(UserRow.username == payload.username)).first()
        if existing is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": "USER_ALREADY_EXISTS", "message": "Username already exists"})

        now = now_utc()
        user_row = UserRow(
            username=payload.username,
            display_name=payload.displayName,
            password_hash=password_hash(payload.password),
            role=UserRole.teacher.value,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        session.add(user_row)
        session.flush()

        auth_session = create_auth_session(session, user_row)
        return envelope(auth_session.model_dump(mode="json"), request)


@app.get("/api/v1/auth/me")
async def get_me(request: Request, current_user: UserEntity = Depends(get_current_user)):
    return envelope(current_user.model_dump(mode="json"), request)


@app.post("/api/v1/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, current_user: UserEntity = Depends(get_current_user)):
    header = request.headers.get("authorization", "")
    _, _, token = header.partition(" ")
    with SessionLocal() as session:
        token_row = session.get(AuthTokenRow, token)
        if token_row is not None:
            session.delete(token_row)
            session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
    return envelope(sorted({question.subject for question in QUESTIONS.values()}), request)


@app.get("/api/v1/meta/tags")
async def list_tags(request: Request, current_user: UserEntity = Depends(require_permission("questions:read"))):
    counter = Counter(str(tag) for question in QUESTIONS.values() for tag in question.tags if tag is not None)
    return envelope(sorted(counter.keys()), request)


@app.post("/api/v1/images/upload")
async def upload_image(
    request: Request,
    payload: ImageUploadPayload,
    current_user: UserEntity = Depends(require_permission("questions:write")),
):
    # Validate MIME type
    allowed_mime_types = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml"}
    if payload.mimeType not in allowed_mime_types:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "VALIDATION_ERROR", "message": f"Unsupported image type: {payload.mimeType}"},
        )
    # Generate a unique filename
    ext = payload.filename.rsplit(".", 1)[-1].lower() if "." in payload.filename else "png"
    safe_name = f"{uuid4().hex}.{ext}"
    # In production, you'd upload to S3/cloud storage. For now, store as data URL.
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
    results = filter_questions(q=q, subject=subject, difficulty=difficulty, question_type=type, tags=tags, has_latex_filter=hasLatex, owner_id=ownerId)
    results = sort_questions(results, sortBy, sortOrder)
    page_data = paginated(results, page, pageSize)
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
    results = filter_questions(q=q, subject=subject, difficulty=difficulty, question_type=type, tags=tags, has_latex_filter=hasLatex, owner_id=current_user.id)
    results = sort_questions(results, sortBy, sortOrder)
    page_data = paginated(results, page, pageSize)
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
    global NEXT_QUESTION_ID
    validate_question_payload(payload)
    # Auto-assign ownerId from current user if not explicitly provided
    if payload.ownerId is None:
        payload.ownerId = current_user.id
    question = normalize_question_payload(payload, question_id=NEXT_QUESTION_ID)
    QUESTIONS[NEXT_QUESTION_ID] = question
    NEXT_QUESTION_ID += 1
    return envelope(question_to_dict(question), request)


@app.patch("/api/v1/questions/{question_id}")
async def update_question(
    request: Request,
    question_id: int,
    payload: QuestionUpdate,
    current_user: UserEntity = Depends(require_permission("questions:write")),
):
    question = get_question_or_404(question_id)
    updated = apply_question_update(question, payload)
    QUESTIONS[question_id] = updated
    return envelope(question_to_dict(updated), request)


@app.delete("/api/v1/questions/{question_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_question(question_id: int, current_user: UserEntity = Depends(require_permission("questions:delete"))):
    get_question_or_404(question_id)
    del QUESTIONS[question_id]
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/api/v1/papers", status_code=status.HTTP_201_CREATED)
async def create_paper(request: Request, payload: PaperCreate, current_user: UserEntity = Depends(require_permission("papers:write"))):
    global NEXT_PAPER_ID
    validate_unique_question_refs(payload.questions, "questions")
    for item in payload.questions:
        get_question_or_404(item.questionId)
    paper = PaperEntity(
        id=NEXT_PAPER_ID,
        title=payload.title,
        subject=payload.subject,
        duration=payload.duration,
        totalMarks=payload.totalMarks,
        questions=[PaperQuestion(**item.model_dump()) for item in sorted(payload.questions, key=lambda item: item.orderNo)],
        status=PaperStatus.draft,
        createdAt=now_utc(),
        updatedAt=now_utc(),
    )
    PAPERS[NEXT_PAPER_ID] = paper
    NEXT_PAPER_ID += 1
    return envelope(paper_with_questions(paper), request)


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


# ---------------------------------------------------------------------------
# Redis health-check
# ---------------------------------------------------------------------------
@app.get("/api/v1/health/redis")
async def redis_health(request: Request):
    try:
        from redis_client import get_redis
        client = get_redis()
        latency_ms = round(client.ping() * 1000 if callable(getattr(client, "ping", None)) else 0, 2)
        info = client.info(section="server")
        return envelope(
            {"status": "connected", "redisVersion": info.get("redis_version"), "latencyMs": latency_ms},
            request,
        )
    except Exception as exc:
        return envelope({"status": "disconnected", "error": str(exc)}, request)


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
    import json as _json

    result = celery.send_task(
        "export_paper",
        args=[paper_id],
        kwargs={
            "question_order": question_order,
            "include_answer": include_answer,
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

