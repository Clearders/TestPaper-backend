from __future__ import annotations

from copy import deepcopy
from typing import Any, cast

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from testpaper_backend.db import PaperDraftCollaboratorRow, PaperDraftCommentRow, PaperDraftRow, SessionLocal, UserRow
from testpaper_backend.schemas import (
    DraftAccessRole,
    DraftCollaboratorRole,
    DraftCommentStatus,
    DraftReviewStatus,
    DraftUserRef,
    PaperDraftCollaboratorCreate,
    PaperDraftCollaboratorEntity,
    PaperDraftCollaboratorUpdate,
    PaperDraftCommentCreate,
    PaperDraftCommentEntity,
    PaperDraftCommentUpdate,
    PaperDraftCreate,
    PaperDraftDetail,
    PaperDraftSummary,
    PaperDraftUpdate,
    UserEntity,
)
from testpaper_backend.security import has_permission
from testpaper_backend.time_utils import now_utc


def _forbidden(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": "FORBIDDEN", "message": message})


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "DRAFT_NOT_FOUND", "message": "Draft not found"})


def _revision_conflict(current_revision: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "DRAFT_REVISION_CONFLICT",
            "message": "Draft changed since it was loaded",
            "currentRevision": current_revision,
        },
    )


def _open_comments_block_approval(open_count: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={
            "code": "DRAFT_OPEN_COMMENTS",
            "message": "Resolve open comments before approving the draft",
            "openCommentCount": open_count,
        },
    )


def user_ref_from_row(row: UserRow | None) -> DraftUserRef | None:
    if row is None:
        return None
    return DraftUserRef(publicId=row.public_id, username=row.username, displayName=row.display_name)


def draft_access_role(row: PaperDraftRow, current_user: UserEntity) -> DraftAccessRole | None:
    if row.owner_id == current_user.id:
        return DraftAccessRole.owner
    if has_permission(current_user, "users:manage"):
        return DraftAccessRole.admin
    for collaborator in row.collaborators:
        if collaborator.user_id == current_user.id:
            return DraftAccessRole(collaborator.role)
    return None


def redact_draft_state_answers(state: dict[str, Any], *, include_answers: bool) -> dict[str, Any]:
    cleaned = deepcopy(state)
    if include_answers:
        return cleaned

    paper = cleaned.get("paper")
    if not isinstance(paper, dict):
        return cleaned
    questions = paper.get("questions")
    if not isinstance(questions, list):
        return cleaned
    for question in questions:
        if not isinstance(question, dict):
            continue
        question.pop("answer", None)
        original = question.get("originalQuestion")
        if isinstance(original, dict):
            original.pop("answer", None)
    return cleaned


def _can_include_answers(current_user: UserEntity) -> bool:
    return has_permission(current_user, "answers:read")


def _summary_from_row(row: PaperDraftRow, current_user: UserEntity) -> PaperDraftSummary:
    access_role = draft_access_role(row, current_user)
    if access_role is None:
        raise _not_found()
    open_comments = [comment for comment in row.comments if comment.status == DraftCommentStatus.open.value]
    return PaperDraftSummary(
        id=row.id,
        publicId=row.public_id,
        name=row.name,
        owner=user_ref_from_row(row.owner),
        accessRole=access_role,
        reviewStatus=DraftReviewStatus(row.review_status),
        revision=row.revision,
        collaboratorCount=len(row.collaborators),
        commentCount=len(row.comments),
        openCommentCount=len(open_comments),
        updatedBy=user_ref_from_row(row.updated_by_user),
        createdAt=row.created_at,
        updatedAt=row.updated_at,
    )


def _collaborator_from_row(row: PaperDraftCollaboratorRow) -> PaperDraftCollaboratorEntity:
    return PaperDraftCollaboratorEntity(
        user=cast(DraftUserRef, user_ref_from_row(row.user)),
        role=DraftCollaboratorRole(row.role),
        createdAt=row.created_at,
        updatedAt=row.updated_at,
    )


def _comment_from_row(row: PaperDraftCommentRow) -> PaperDraftCommentEntity:
    return PaperDraftCommentEntity(
        id=row.id,
        publicId=row.public_id,
        questionPublicId=row.question_public_id,
        message=row.message,
        status=DraftCommentStatus(row.status),
        author=user_ref_from_row(row.author),
        createdAt=row.created_at,
        updatedAt=row.updated_at,
    )


def _detail_from_row(row: PaperDraftRow, current_user: UserEntity) -> PaperDraftDetail:
    summary = _summary_from_row(row, current_user)
    return PaperDraftDetail(
        **summary.model_dump(),
        state=redact_draft_state_answers(row.state or {}, include_answers=_can_include_answers(current_user)),
        collaborators=[_collaborator_from_row(item) for item in sorted(row.collaborators, key=lambda item: item.created_at)],
        comments=[_comment_from_row(item) for item in sorted(row.comments, key=lambda item: item.created_at)],
    )


def _draft_options():
    return (
        selectinload(PaperDraftRow.owner),
        selectinload(PaperDraftRow.updated_by_user),
        selectinload(PaperDraftRow.collaborators).selectinload(PaperDraftCollaboratorRow.user),
        selectinload(PaperDraftRow.comments).selectinload(PaperDraftCommentRow.author),
    )


def _get_draft_row(session, draft_public_id: str) -> PaperDraftRow:
    row = session.scalars(select(PaperDraftRow).options(*_draft_options()).where(PaperDraftRow.public_id == draft_public_id)).first()
    if row is None:
        raise _not_found()
    return row


def _ensure_read_access(row: PaperDraftRow, current_user: UserEntity) -> DraftAccessRole:
    role = draft_access_role(row, current_user)
    if role is None:
        raise _not_found()
    return role


def _ensure_edit_access(row: PaperDraftRow, current_user: UserEntity) -> DraftAccessRole:
    role = _ensure_read_access(row, current_user)
    if role in {DraftAccessRole.owner, DraftAccessRole.admin, DraftAccessRole.editor}:
        return role
    raise _forbidden("You can view this draft but cannot edit it")


def _ensure_manage_access(row: PaperDraftRow, current_user: UserEntity) -> DraftAccessRole:
    role = _ensure_read_access(row, current_user)
    if role in {DraftAccessRole.owner, DraftAccessRole.admin}:
        return role
    raise _forbidden("Only draft owners can manage sharing or delete the draft")


def list_accessible_drafts(current_user: UserEntity) -> list[PaperDraftSummary]:
    with SessionLocal() as session:
        statement = select(PaperDraftRow).options(*_draft_options()).order_by(PaperDraftRow.updated_at.desc())
        if not has_permission(current_user, "users:manage"):
            statement = statement.where(
                or_(
                    PaperDraftRow.owner_id == current_user.id,
                    PaperDraftRow.collaborators.any(PaperDraftCollaboratorRow.user_id == current_user.id),
                )
            )
        rows = session.scalars(statement).all()
        return [_summary_from_row(row, current_user) for row in rows]


def create_shared_draft(payload: PaperDraftCreate, current_user: UserEntity) -> PaperDraftDetail:
    now = now_utc()
    with SessionLocal() as session:
        row = PaperDraftRow(
            name=payload.name,
            owner_id=current_user.id,
            state=deepcopy(payload.state),
            review_status=payload.reviewStatus.value,
            revision=1,
            updated_by=current_user.id,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        row = _get_draft_row(session, row.public_id)
        return _detail_from_row(row, current_user)


def get_shared_draft(draft_public_id: str, current_user: UserEntity) -> PaperDraftDetail:
    with SessionLocal() as session:
        row = _get_draft_row(session, draft_public_id)
        _ensure_read_access(row, current_user)
        return _detail_from_row(row, current_user)


def update_shared_draft(draft_public_id: str, payload: PaperDraftUpdate, current_user: UserEntity) -> PaperDraftDetail:
    with SessionLocal() as session:
        row = _get_draft_row(session, draft_public_id)
        role = _ensure_edit_access(row, current_user)
        if payload.baseRevision != row.revision:
            raise _revision_conflict(row.revision)

        if payload.name is not None and payload.name != row.name:
            if role not in {DraftAccessRole.owner, DraftAccessRole.admin}:
                raise _forbidden("Only draft owners can rename shared drafts")
            row.name = payload.name

        if payload.state is not None:
            row.state = deepcopy(payload.state)

        if payload.reviewStatus is not None:
            open_comment_count = sum(1 for comment in row.comments if comment.status == DraftCommentStatus.open.value)
            if payload.reviewStatus == DraftReviewStatus.approved and open_comment_count:
                raise _open_comments_block_approval(open_comment_count)
            if role in {DraftAccessRole.owner, DraftAccessRole.admin} or (
                role == DraftAccessRole.editor and payload.reviewStatus == DraftReviewStatus.in_review
            ):
                row.review_status = payload.reviewStatus.value
            else:
                raise _forbidden("You cannot set this draft review status")

        row.revision += 1
        row.updated_by = current_user.id
        row.updated_at = now_utc()
        session.commit()
        row = _get_draft_row(session, draft_public_id)
        return _detail_from_row(row, current_user)


def delete_shared_draft(draft_public_id: str, current_user: UserEntity) -> None:
    with SessionLocal() as session:
        row = _get_draft_row(session, draft_public_id)
        _ensure_manage_access(row, current_user)
        session.delete(row)
        session.commit()


def upsert_draft_collaborator(
    draft_public_id: str,
    payload: PaperDraftCollaboratorCreate,
    current_user: UserEntity,
) -> PaperDraftDetail:
    with SessionLocal() as session:
        row = _get_draft_row(session, draft_public_id)
        _ensure_manage_access(row, current_user)
        target = session.scalars(select(UserRow).where(UserRow.username == payload.username)).first()
        if target is None or not target.is_active:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "USER_NOT_FOUND", "message": "User not found"})
        if target.id == row.owner_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "VALIDATION_ERROR", "message": "The draft owner is already the owner, not a collaborator"},
            )

        now = now_utc()
        collaborator = next((item for item in row.collaborators if item.user_id == target.id), None)
        if collaborator is None:
            session.add(
                PaperDraftCollaboratorRow(
                    draft_id=row.id,
                    user_id=target.id,
                    role=payload.role.value,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            collaborator.role = payload.role.value
            collaborator.updated_at = now
        row.updated_by = current_user.id
        row.updated_at = now
        session.commit()
        session.expire_all()
        row = _get_draft_row(session, draft_public_id)
        return _detail_from_row(row, current_user)


def update_draft_collaborator(
    draft_public_id: str,
    user_public_id: str,
    payload: PaperDraftCollaboratorUpdate,
    current_user: UserEntity,
) -> PaperDraftDetail:
    with SessionLocal() as session:
        row = _get_draft_row(session, draft_public_id)
        _ensure_manage_access(row, current_user)
        target = next((item for item in row.collaborators if item.user.public_id == user_public_id), None)
        if target is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "COLLABORATOR_NOT_FOUND", "message": "Collaborator not found"},
            )
        now = now_utc()
        target.role = payload.role.value
        target.updated_at = now
        row.updated_by = current_user.id
        row.updated_at = now
        session.commit()
        session.expire_all()
        row = _get_draft_row(session, draft_public_id)
        return _detail_from_row(row, current_user)


def delete_draft_collaborator(draft_public_id: str, user_public_id: str, current_user: UserEntity) -> PaperDraftDetail:
    with SessionLocal() as session:
        row = _get_draft_row(session, draft_public_id)
        _ensure_manage_access(row, current_user)
        target = next((item for item in row.collaborators if item.user.public_id == user_public_id), None)
        if target is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "COLLABORATOR_NOT_FOUND", "message": "Collaborator not found"},
            )
        now = now_utc()
        session.delete(target)
        row.updated_by = current_user.id
        row.updated_at = now
        session.commit()
        session.expire_all()
        row = _get_draft_row(session, draft_public_id)
        return _detail_from_row(row, current_user)


def create_draft_comment(
    draft_public_id: str,
    payload: PaperDraftCommentCreate,
    current_user: UserEntity,
) -> PaperDraftDetail:
    with SessionLocal() as session:
        row = _get_draft_row(session, draft_public_id)
        _ensure_read_access(row, current_user)
        now = now_utc()
        session.add(
            PaperDraftCommentRow(
                draft_id=row.id,
                question_public_id=payload.questionPublicId,
                message=payload.message,
                status=DraftCommentStatus.open.value,
                author_id=current_user.id,
                created_at=now,
                updated_at=now,
            )
        )
        row.updated_by = current_user.id
        row.updated_at = now
        session.commit()
        row = _get_draft_row(session, draft_public_id)
        return _detail_from_row(row, current_user)


def update_draft_comment(
    draft_public_id: str,
    comment_public_id: str,
    payload: PaperDraftCommentUpdate,
    current_user: UserEntity,
) -> PaperDraftDetail:
    with SessionLocal() as session:
        row = _get_draft_row(session, draft_public_id)
        role = _ensure_read_access(row, current_user)
        comment = next((item for item in row.comments if item.public_id == comment_public_id), None)
        if comment is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "COMMENT_NOT_FOUND", "message": "Comment not found"})
        can_update = comment.author_id == current_user.id or role in {DraftAccessRole.owner, DraftAccessRole.admin, DraftAccessRole.editor}
        if not can_update:
            raise _forbidden("You can only update your own comments")

        now = now_utc()
        if payload.message is not None:
            comment.message = payload.message
        if payload.status is not None:
            comment.status = payload.status.value
        comment.updated_at = now
        row.updated_by = current_user.id
        row.updated_at = now
        session.commit()
        row = _get_draft_row(session, draft_public_id)
        return _detail_from_row(row, current_user)
