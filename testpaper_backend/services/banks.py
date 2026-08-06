from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any, cast
from uuid import uuid4

from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from testpaper_backend.db import (
    BankPublicationRow,
    BankSubscriptionRow,
    QuestionBankItemRow,
    QuestionBankMemberRow,
    QuestionBankRow,
    QuestionRow,
    SessionLocal,
    UserRow,
)
from testpaper_backend.repositories import question_entity_to_row_kwargs, question_row_to_entity
from testpaper_backend.schemas import (
    BankAccessRole,
    BankCreate,
    BankForkRequest,
    BankItemAdd,
    BankMemberCreate,
    BankMemberEntity,
    BankMemberUpdate,
    BankPublicationEntity,
    BankRole,
    BankSubscriptionEntity,
    BankUpdate,
    BankUserRef,
    BankVersionSummary,
    BankVisibility,
    QuestionBankEntity,
    QuestionBankSummary,
    QuestionBase,
    QuestionEntity,
    QuestionType,
    UserEntity,
)
from testpaper_backend.security import has_permission
from testpaper_backend.services.questions import normalize_question_payload
from testpaper_backend.time_utils import now_utc


def _forbidden(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": "FORBIDDEN", "message": message})


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "BANK_NOT_FOUND", "message": "Bank not found"})


def _not_found_error(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": code, "message": message})


def _validation_error(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail={"code": code, "message": message})


def _conflict(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": code, "message": message})


def _bank_item_exists(bank_public_id: str, question_public_ids: list[str]) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "BANK_ITEM_EXISTS",
            "message": "One or more questions already exist in this bank",
            "details": {"bankPublicId": bank_public_id, "questionPublicIds": question_public_ids},
        },
    )


def bank_user_ref(row: UserRow | None) -> BankUserRef | None:
    if row is None:
        return None
    return BankUserRef(publicId=row.public_id, username=row.username, displayName=row.display_name)


def bank_access_role(bank: QuestionBankRow, current_user: UserEntity) -> BankAccessRole | None:
    if bank.owner_id == current_user.id:
        return BankAccessRole.owner
    if has_permission(current_user, "users:manage"):
        return BankAccessRole.admin
    for member in bank.members:
        if member.user_id == current_user.id:
            return BankAccessRole(member.role)
    return None


def _redacted_answer(question_type: str) -> str | list[str]:
    if question_type == QuestionType.multiple_choice.value:
        return ["[redacted]"]
    return "[redacted]"


def _member_from_row(row: QuestionBankMemberRow) -> BankMemberEntity:
    return BankMemberEntity(
        user=cast(BankUserRef, bank_user_ref(row.user)),
        role=BankRole(row.role),
        createdAt=row.created_at,
        updatedAt=row.updated_at,
    )


def _summary_from_row(row: QuestionBankRow, current_user: UserEntity) -> QuestionBankSummary:
    role = _ensure_read_access(row, current_user)
    return QuestionBankSummary(
        id=row.id,
        publicId=row.public_id,
        name=row.name,
        description=row.description,
        visibility=BankVisibility(row.visibility),
        owner=bank_user_ref(row.owner),
        accessRole=role,
        version=row.publications[-1].version if row.publications else None,
        itemCount=len(row.items),
        memberCount=len(row.members),
        subscriberCount=len(row.subscriptions),
        createdAt=row.created_at,
        updatedAt=row.updated_at,
    )


def _detail_from_row(row: QuestionBankRow, current_user: UserEntity) -> QuestionBankEntity:
    summary = _summary_from_row(row, current_user)
    return QuestionBankEntity(
        **summary.model_dump(),
        members=[_member_from_row(item) for item in sorted(row.members, key=lambda item: item.created_at)],
    )


def _bank_options():
    return (
        selectinload(QuestionBankRow.owner),
        selectinload(QuestionBankRow.items).selectinload(QuestionBankItemRow.question),
        selectinload(QuestionBankRow.members).selectinload(QuestionBankMemberRow.user),
        selectinload(QuestionBankRow.publications).selectinload(BankPublicationRow.created_by_user),
        selectinload(QuestionBankRow.subscriptions),
    )


def _get_bank_row(session, bank_public_id: str) -> QuestionBankRow:
    row = session.scalars(select(QuestionBankRow).options(*_bank_options()).where(QuestionBankRow.public_id == bank_public_id)).first()
    if row is None:
        raise _not_found()
    return row


def _ensure_read_access(bank: QuestionBankRow, current_user: UserEntity) -> BankAccessRole:
    role = bank_access_role(bank, current_user)
    if role is not None:
        return role
    if bank.visibility == BankVisibility.public.value:
        return BankAccessRole.viewer
    raise _not_found()


def _ensure_edit_access(bank: QuestionBankRow, current_user: UserEntity) -> BankAccessRole:
    role = _ensure_read_access(bank, current_user)
    if role in {BankAccessRole.owner, BankAccessRole.admin, BankAccessRole.editor}:
        return role
    raise _forbidden("You can view this bank but cannot edit it")


def _ensure_manage_access(bank: QuestionBankRow, current_user: UserEntity) -> BankAccessRole:
    role = _ensure_read_access(bank, current_user)
    if role in {BankAccessRole.owner, BankAccessRole.admin}:
        return role
    raise _forbidden("Only the bank owner or an administrator can manage this bank")


def _can_include_answers(current_user: UserEntity) -> bool:
    return has_permission(current_user, "answers:read")


def _question_entity(question: QuestionRow, *, include_answers: bool) -> QuestionEntity:
    entity = question_row_to_entity(question)
    if include_answers:
        return entity
    return entity.model_copy(update={"answer": _redacted_answer(question.type)})


def _add_bank_item_rows(session, bank: QuestionBankRow, question_rows: list[QuestionRow], actor: UserEntity) -> None:
    now = now_utc()
    existing = {item.question_id for item in bank.items}
    for question in question_rows:
        if question.id in existing:
            continue
        session.add(QuestionBankItemRow(bank_id=bank.id, question_id=question.id, added_by=actor.id, created_at=now))


def list_visible_banks(current_user: UserEntity) -> list[QuestionBankSummary]:
    with SessionLocal() as session:
        statement = select(QuestionBankRow).options(*_bank_options()).order_by(QuestionBankRow.updated_at.desc())
        if not has_permission(current_user, "users:manage"):
            statement = statement.where(
                or_(
                    QuestionBankRow.owner_id == current_user.id,
                    QuestionBankRow.visibility == BankVisibility.public.value,
                    QuestionBankRow.members.any(QuestionBankMemberRow.user_id == current_user.id),
                )
            )
        rows = session.scalars(statement).all()
        return [_summary_from_row(row, current_user) for row in rows]


def create_bank(payload: BankCreate, current_user: UserEntity) -> QuestionBankEntity:
    if not has_permission(current_user, "banks:write"):
        raise _forbidden("You need the banks:write permission to create a bank")
    now = now_utc()
    with SessionLocal() as session:
        row = QuestionBankRow(
            public_id=str(uuid4()),
            name=payload.name,
            description=payload.description,
            owner_id=current_user.id,
            visibility=payload.visibility.value,
            latest_version=0,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.commit()
        row = _get_bank_row(session, row.public_id)
        return _detail_from_row(row, current_user)


def get_bank_detail(bank_public_id: str, current_user: UserEntity) -> QuestionBankEntity:
    with SessionLocal() as session:
        row = _get_bank_row(session, bank_public_id)
        _ensure_read_access(row, current_user)
        return _detail_from_row(row, current_user)


def update_bank(bank_public_id: str, payload: BankUpdate, current_user: UserEntity) -> QuestionBankEntity:
    with SessionLocal() as session:
        row = _get_bank_row(session, bank_public_id)
        role = _ensure_manage_access(row, current_user)
        if payload.visibility is not None and role != BankAccessRole.owner:
            raise _forbidden("Only the bank owner can change visibility")
        if payload.name is not None:
            row.name = payload.name
        if payload.description is not None:
            row.description = payload.description
        if payload.visibility is not None:
            row.visibility = payload.visibility.value
        row.updated_at = now_utc()
        session.commit()
        row = _get_bank_row(session, bank_public_id)
        return _detail_from_row(row, current_user)


def delete_bank(bank_public_id: str, current_user: UserEntity) -> None:
    with SessionLocal() as session:
        row = _get_bank_row(session, bank_public_id)
        _ensure_manage_access(row, current_user)
        if not has_permission(current_user, "banks:delete"):
            raise _forbidden("You need the banks:delete permission to delete a bank")
        session.delete(row)
        session.commit()


def add_bank_items(bank_public_id: str, payload: BankItemAdd, current_user: UserEntity) -> QuestionBankEntity:
    question_ids = payload.questionIds
    if len(question_ids) != len(set(question_ids)):
        raise _validation_error("VALIDATION_ERROR", "questionIds must not contain duplicate question IDs")
    with SessionLocal() as session:
        row = _get_bank_row(session, bank_public_id)
        _ensure_edit_access(row, current_user)
        if not has_permission(current_user, "questions:read"):
            raise _forbidden("You need the questions:read permission to add questions")
        questions = session.scalars(select(QuestionRow).where(QuestionRow.public_id.in_(question_ids))).all()
        found = {question.public_id: question for question in questions}
        missing = [question_id for question_id in question_ids if question_id not in found]
        if missing:
            raise _not_found_error("QUESTION_NOT_FOUND", f"Question '{missing[0]}' not found")
        existing_ids = {item.question_id for item in row.items}
        conflicts = [question_id for question_id in question_ids if found[question_id].id in existing_ids]
        if conflicts:
            raise _bank_item_exists(row.public_id, conflicts)
        _add_bank_item_rows(session, row, [found[question_id] for question_id in question_ids], current_user)
        session.commit()
        row = _get_bank_row(session, bank_public_id)
        return _detail_from_row(row, current_user)


def remove_bank_item(bank_public_id: str, question_public_id: str, current_user: UserEntity) -> QuestionBankEntity:
    with SessionLocal() as session:
        row = _get_bank_row(session, bank_public_id)
        _ensure_edit_access(row, current_user)
        target = next((item for item in row.items if item.question.public_id == question_public_id), None)
        if target is None:
            raise _not_found_error("BANK_ITEM_NOT_FOUND", "Question is not in this bank")
        session.delete(target)
        session.commit()
        row = _get_bank_row(session, bank_public_id)
        return _detail_from_row(row, current_user)


def list_bank_questions(bank_public_id: str, current_user: UserEntity) -> list[QuestionEntity]:
    with SessionLocal() as session:
        row = _get_bank_row(session, bank_public_id)
        _ensure_read_access(row, current_user)
        include_answers = _can_include_answers(current_user)
        items = sorted(row.items, key=lambda item: item.created_at)
        return [_question_entity(item.question, include_answers=include_answers) for item in items]


def add_bank_member(bank_public_id: str, payload: BankMemberCreate, current_user: UserEntity) -> QuestionBankEntity:
    with SessionLocal() as session:
        row = _get_bank_row(session, bank_public_id)
        _ensure_manage_access(row, current_user)
        target = session.scalars(select(UserRow).where(UserRow.username == payload.username)).first()
        if target is None or not target.is_active:
            raise _not_found_error("USER_NOT_FOUND", "User not found")
        if target.id == row.owner_id:
            raise _validation_error("BANK_OWNER_CANNOT_BE_MEMBER", "The bank owner cannot be added as a member")
        if any(member.user_id == target.id for member in row.members):
            raise _validation_error("BANK_MEMBER_EXISTS", "User is already a member of this bank")
        now = now_utc()
        session.add(
            QuestionBankMemberRow(
                bank_id=row.id,
                user_id=target.id,
                role=payload.role.value,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
        row = _get_bank_row(session, bank_public_id)
        return _detail_from_row(row, current_user)


def update_bank_member_role(
    bank_public_id: str, user_public_id: str, payload: BankMemberUpdate, current_user: UserEntity
) -> QuestionBankEntity:
    with SessionLocal() as session:
        row = _get_bank_row(session, bank_public_id)
        _ensure_manage_access(row, current_user)
        member = next((item for item in row.members if item.user.public_id == user_public_id), None)
        if member is None:
            raise _not_found_error("MEMBER_NOT_FOUND", "Member not found")
        member.role = payload.role.value
        member.updated_at = now_utc()
        session.commit()
        row = _get_bank_row(session, bank_public_id)
        return _detail_from_row(row, current_user)


def remove_bank_member(bank_public_id: str, user_public_id: str, current_user: UserEntity) -> QuestionBankEntity:
    with SessionLocal() as session:
        row = _get_bank_row(session, bank_public_id)
        _ensure_manage_access(row, current_user)
        member = next((item for item in row.members if item.user.public_id == user_public_id), None)
        if member is None:
            raise _not_found_error("MEMBER_NOT_FOUND", "Member not found")
        session.delete(member)
        session.commit()
        row = _get_bank_row(session, bank_public_id)
        return _detail_from_row(row, current_user)


def redact_bank_snapshot_answers(snapshot_state: dict[str, Any], *, include_answers: bool) -> dict[str, Any]:
    cleaned = deepcopy(snapshot_state)
    if include_answers:
        return cleaned
    items = cleaned.get("items")
    if not isinstance(items, list):
        return cleaned
    for item in items:
        if not isinstance(item, dict):
            continue
        data = item.get("data")
        if isinstance(data, dict):
            data["answer"] = _redacted_answer(str(data.get("type") or ""))
    return cleaned


def load_bank_snapshot(publication: BankPublicationRow, current_user: UserEntity) -> dict[str, Any]:
    return redact_bank_snapshot_answers(publication.state or {}, include_answers=_can_include_answers(current_user))


def _build_snapshot_state(row: QuestionBankRow, version: int, published_at: datetime) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for item in sorted(row.items, key=lambda item: item.created_at):
        question = item.question
        items.append(
            {
                "publicId": question.public_id,
                "data": {
                    "type": question.type,
                    "subjects": list(question.subjects or []),
                    "difficulty": question.difficulty,
                    "tags": list(question.tags or []),
                    "text": question.text,
                    "options": list(question.options) if question.options is not None else None,
                    "answer": deepcopy(question.answer),
                    "hasLatex": question.has_latex,
                    "source": question.source,
                    "essayBlankSpace": deepcopy(question.essay_blank_space),
                    "images": deepcopy(question.images or []),
                    "scoreWeight": question.score_weight,
                },
            }
        )
    return {
        "version": version,
        "visibility": row.visibility,
        "publishedAt": published_at.isoformat(),
        "bank": {"publicId": row.public_id, "name": row.name, "description": row.description},
        "items": items,
    }


def publish_bank(bank_public_id: str, current_user: UserEntity) -> QuestionBankEntity:
    with SessionLocal() as session:
        row = _get_bank_row(session, bank_public_id)
        _ensure_manage_access(row, current_user)
        if not has_permission(current_user, "banks:publish"):
            raise _forbidden("You need the banks:publish permission to publish a bank")
        if not row.items:
            raise _validation_error("BANK_PUBLISH_EMPTY", "A bank must contain at least one question before publishing")
        if row.publications:
            raise _conflict("BANK_ALREADY_PUBLISHED", "The bank is already published; withdraw it before publishing again")
        now = now_utc()
        new_version = row.latest_version + 1
        state = _build_snapshot_state(row, new_version, now)
        session.add(
            BankPublicationRow(
                public_id=str(uuid4()),
                bank_id=row.id,
                version=new_version,
                state=state,
                created_by=current_user.id,
                created_at=now,
            )
        )
        row.latest_version = new_version
        session.commit()
        row = _get_bank_row(session, bank_public_id)
        return _detail_from_row(row, current_user)


def withdraw_bank(bank_public_id: str, current_user: UserEntity) -> QuestionBankEntity:
    with SessionLocal() as session:
        row = _get_bank_row(session, bank_public_id)
        _ensure_manage_access(row, current_user)
        if not has_permission(current_user, "banks:publish"):
            raise _forbidden("You need the banks:publish permission to withdraw a bank")
        if not row.publications:
            raise _conflict("BANK_NOT_PUBLISHED", "The bank is not published")
        session.delete(row.publications[-1])
        session.commit()
        row = _get_bank_row(session, bank_public_id)
        return _detail_from_row(row, current_user)


def list_bank_versions(bank_public_id: str, current_user: UserEntity) -> list[BankVersionSummary]:
    with SessionLocal() as session:
        row = _get_bank_row(session, bank_public_id)
        _ensure_read_access(row, current_user)
        return [
            BankVersionSummary(
                id=publication.id,
                publicId=publication.public_id,
                version=publication.version,
                createdBy=bank_user_ref(publication.created_by_user),
                createdAt=publication.created_at,
            )
            for publication in row.publications
        ]


def get_bank_version(bank_public_id: str, version: int, current_user: UserEntity) -> BankPublicationEntity:
    with SessionLocal() as session:
        row = _get_bank_row(session, bank_public_id)
        _ensure_read_access(row, current_user)
        publication = next((item for item in row.publications if item.version == version), None)
        if publication is None:
            raise _not_found_error("BANK_VERSION_NOT_FOUND", f"Bank version {version} not found")
        return BankPublicationEntity(
            id=publication.id,
            publicId=publication.public_id,
            bankId=publication.bank_id,
            version=publication.version,
            state=load_bank_snapshot(publication, current_user),
            createdBy=bank_user_ref(publication.created_by_user),
            createdAt=publication.created_at,
        )


def subscribe_bank(bank_public_id: str, current_user: UserEntity) -> BankSubscriptionEntity:
    with SessionLocal() as session:
        row = _get_bank_row(session, bank_public_id)
        _ensure_read_access(row, current_user)
        if row.visibility == BankVisibility.private.value:
            raise _validation_error("BANK_SUBSCRIBE_PRIVATE", "Private banks cannot be subscribed")
        existing = next((item for item in row.subscriptions if item.user_id == current_user.id), None)
        if existing is not None:
            return BankSubscriptionEntity(bankId=row.id, userId=current_user.id, createdAt=existing.created_at)
        now = now_utc()
        session.add(BankSubscriptionRow(bank_id=row.id, user_id=current_user.id, created_at=now))
        session.commit()
        return BankSubscriptionEntity(bankId=row.id, userId=current_user.id, createdAt=now)


def unsubscribe_bank(bank_public_id: str, current_user: UserEntity) -> None:
    with SessionLocal() as session:
        row = _get_bank_row(session, bank_public_id)
        _ensure_read_access(row, current_user)
        existing = next((item for item in row.subscriptions if item.user_id == current_user.id), None)
        if existing is not None:
            session.delete(existing)
            session.commit()


def _question_from_snapshot(data: dict[str, Any], current_user: UserEntity, created_at: datetime) -> QuestionRow:
    allowed_fields = set(QuestionBase.model_fields)
    try:
        base = QuestionBase(**{key: value for key, value in data.items() if key in allowed_fields})
    except ValidationError as exc:
        raise _validation_error(
            "VALIDATION_ERROR",
            "Published question data is invalid",
        ) from exc
    entity = normalize_question_payload(base, question_id=0, created_at=created_at)
    entity.ownerId = current_user.id
    row_kwargs = question_entity_to_row_kwargs(entity)
    row_kwargs.pop("id", None)
    return QuestionRow(**row_kwargs)


def fork_bank(bank_public_id: str, payload: BankForkRequest, current_user: UserEntity) -> QuestionBankEntity:
    with SessionLocal() as session:
        row = _get_bank_row(session, bank_public_id)
        _ensure_read_access(row, current_user)
        if not row.publications:
            raise _conflict("BANK_NOT_PUBLISHED", "The bank has no published version to fork")
        version = payload.version if payload.version is not None else row.publications[-1].version
        publication = next((item for item in row.publications if item.version == version), None)
        if publication is None:
            raise _not_found_error("BANK_VERSION_NOT_FOUND", f"Bank version {version} not found")
        snapshot = load_bank_snapshot(publication, current_user)
        now = now_utc()
        new_bank = QuestionBankRow(
            public_id=str(uuid4()),
            name=f"{row.name} (fork)",
            description=row.description,
            owner_id=current_user.id,
            visibility=BankVisibility.private.value,
            latest_version=0,
            created_at=now,
            updated_at=now,
        )
        session.add(new_bank)
        session.flush()
        copied_questions: list[QuestionRow] = []
        for item in snapshot.get("items") or []:
            if not isinstance(item, dict):
                continue
            data = item.get("data")
            if not isinstance(data, dict):
                continue
            question = _question_from_snapshot(data, current_user, now)
            session.add(question)
            session.flush()
            copied_questions.append(question)
        _add_bank_item_rows(session, new_bank, copied_questions, current_user)
        session.commit()
        new_row = _get_bank_row(session, new_bank.public_id)
        return _detail_from_row(new_row, current_user)
