from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Request, Response, status

from testpaper_backend.api.dependencies import PapersReadDep, PapersWriteDep, RateLimitWriteDep
from testpaper_backend.core.responses import envelope
from testpaper_backend.documents.paper_docx import DOCX_MEDIA_TYPE, build_paper_docx, docx_filename, resolve_layout_density
from testpaper_backend.schemas import (
    Envelope,
    LayoutDensity,
    PaperDraftCollaboratorCreate,
    PaperDraftCollaboratorUpdate,
    PaperDraftCommentCreate,
    PaperDraftCommentUpdate,
    PaperDraftCreate,
    PaperDraftDetail,
    PaperDraftSummary,
    PaperDraftUpdate,
    QuestionOrder,
)
from testpaper_backend.security import has_permission
from testpaper_backend.services.drafts import (
    create_draft_comment,
    create_shared_draft,
    delete_draft_collaborator,
    delete_shared_draft,
    get_shared_draft,
    list_accessible_drafts,
    update_draft_collaborator,
    update_draft_comment,
    update_shared_draft,
    upsert_draft_collaborator,
)
from testpaper_backend.services.papers import order_export_questions
from testpaper_backend.services.realtime import realtime

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/drafts", tags=["drafts"])


def _enum_value(enum_cls, value: Any, fallback):
    try:
        return enum_cls(value)
    except ValueError:
        return fallback


def _draft_docx_response(detail: PaperDraftDetail, *, include_answer: bool) -> Response:
    state = detail.state
    paper_state = state.get("paper") if isinstance(state.get("paper"), dict) else {}
    questions = paper_state.get("questions") if isinstance(paper_state.get("questions"), list) else []
    questions = [question.copy() for question in questions if isinstance(question, dict)]
    if not include_answer:
        for question in questions:
            question.pop("answer", None)

    question_order = _enum_value(QuestionOrder, state.get("exportMode"), QuestionOrder.paper)
    layout_density = _enum_value(LayoutDensity, state.get("layoutDensity"), LayoutDensity.auto)
    ordered_questions = order_export_questions(questions, question_order)
    effective_layout_density = resolve_layout_density(ordered_questions, layout_density)
    paper = SimpleNamespace(
        title=str(paper_state.get("title") or detail.name),
        subject=str(paper_state.get("subject") or ""),
        duration=int(paper_state.get("duration") or 60),
        totalMarks=int(paper_state.get("totalMarks") or 100),
    )
    file_bytes = build_paper_docx(paper, ordered_questions, include_answer=include_answer, layout_density=layout_density)
    filename = docx_filename(paper.title)
    ascii_filename = docx_filename(paper.title.encode("ascii", "ignore").decode("ascii") or "examination-paper")
    return Response(
        content=file_bytes,
        media_type=DOCX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f"attachment; filename=\"{ascii_filename}\"; filename*=UTF-8''{quote(filename)}",
            "X-Export-Format": "docx",
            "X-Layout-Density": effective_layout_density,
            "X-Cloud-Draft-Export": "true",
        },
    )


def _draft_event_payload(detail: PaperDraftDetail, actor_id: int) -> dict[str, Any]:
    return {
        "draftId": detail.publicId,
        "revision": detail.revision,
        "reviewStatus": detail.reviewStatus.value,
        "actorId": actor_id,
    }


@router.get("", response_model=Envelope[list[PaperDraftSummary]])
def list_drafts(request: Request, current_user: PapersReadDep):
    drafts = list_accessible_drafts(current_user)
    return envelope([draft.model_dump(mode="json") for draft in drafts], request)


@router.post("", response_model=Envelope[PaperDraftDetail], status_code=status.HTTP_201_CREATED)
def create_draft(
    request: Request,
    background_tasks: BackgroundTasks,
    payload: PaperDraftCreate,
    current_user: PapersWriteDep,
    _: RateLimitWriteDep,
):
    detail = create_shared_draft(payload, current_user)
    background_tasks.add_task(realtime.broadcast, "draft.updated", _draft_event_payload(detail, current_user.id))
    logger.info("Shared draft created: %s", detail.publicId)
    return envelope(detail.model_dump(mode="json"), request)


@router.get("/{draft_public_id}", response_model=Envelope[PaperDraftDetail])
def get_draft(request: Request, draft_public_id: str, current_user: PapersReadDep):
    detail = get_shared_draft(draft_public_id, current_user)
    return envelope(detail.model_dump(mode="json"), request)


@router.patch("/{draft_public_id}", response_model=Envelope[PaperDraftDetail])
def update_draft(
    request: Request,
    background_tasks: BackgroundTasks,
    draft_public_id: str,
    payload: PaperDraftUpdate,
    current_user: PapersReadDep,
    _: RateLimitWriteDep,
):
    detail = update_shared_draft(draft_public_id, payload, current_user)
    event_name = "draft.review.updated" if payload.reviewStatus is not None else "draft.updated"
    background_tasks.add_task(realtime.broadcast, event_name, _draft_event_payload(detail, current_user.id))
    return envelope(detail.model_dump(mode="json"), request)


@router.delete("/{draft_public_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_draft(draft_public_id: str, current_user: PapersReadDep, _: RateLimitWriteDep):
    delete_shared_draft(draft_public_id, current_user)
    logger.info("Shared draft deleted: %s", draft_public_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{draft_public_id}/collaborators", response_model=Envelope[PaperDraftDetail])
def create_or_update_collaborator(
    request: Request,
    background_tasks: BackgroundTasks,
    draft_public_id: str,
    payload: PaperDraftCollaboratorCreate,
    current_user: PapersReadDep,
    _: RateLimitWriteDep,
):
    detail = upsert_draft_collaborator(draft_public_id, payload, current_user)
    background_tasks.add_task(realtime.broadcast, "draft.updated", _draft_event_payload(detail, current_user.id))
    return envelope(detail.model_dump(mode="json"), request)


@router.patch("/{draft_public_id}/collaborators/{user_public_id}", response_model=Envelope[PaperDraftDetail])
def patch_collaborator(
    request: Request,
    background_tasks: BackgroundTasks,
    draft_public_id: str,
    user_public_id: str,
    payload: PaperDraftCollaboratorUpdate,
    current_user: PapersReadDep,
    _: RateLimitWriteDep,
):
    detail = update_draft_collaborator(draft_public_id, user_public_id, payload, current_user)
    background_tasks.add_task(realtime.broadcast, "draft.updated", _draft_event_payload(detail, current_user.id))
    return envelope(detail.model_dump(mode="json"), request)


@router.delete("/{draft_public_id}/collaborators/{user_public_id}", response_model=Envelope[PaperDraftDetail])
def remove_collaborator(
    request: Request,
    background_tasks: BackgroundTasks,
    draft_public_id: str,
    user_public_id: str,
    current_user: PapersReadDep,
    _: RateLimitWriteDep,
):
    detail = delete_draft_collaborator(draft_public_id, user_public_id, current_user)
    background_tasks.add_task(realtime.broadcast, "draft.updated", _draft_event_payload(detail, current_user.id))
    return envelope(detail.model_dump(mode="json"), request)


@router.post("/{draft_public_id}/comments", response_model=Envelope[PaperDraftDetail], status_code=status.HTTP_201_CREATED)
def create_comment(
    request: Request,
    background_tasks: BackgroundTasks,
    draft_public_id: str,
    payload: PaperDraftCommentCreate,
    current_user: PapersReadDep,
    _: RateLimitWriteDep,
):
    detail = create_draft_comment(draft_public_id, payload, current_user)
    background_tasks.add_task(realtime.broadcast, "draft.comment.created", _draft_event_payload(detail, current_user.id))
    return envelope(detail.model_dump(mode="json"), request)


@router.patch("/{draft_public_id}/comments/{comment_public_id}", response_model=Envelope[PaperDraftDetail])
def patch_comment(
    request: Request,
    background_tasks: BackgroundTasks,
    draft_public_id: str,
    comment_public_id: str,
    payload: PaperDraftCommentUpdate,
    current_user: PapersReadDep,
    _: RateLimitWriteDep,
):
    detail = update_draft_comment(draft_public_id, comment_public_id, payload, current_user)
    background_tasks.add_task(realtime.broadcast, "draft.comment.updated", _draft_event_payload(detail, current_user.id))
    return envelope(detail.model_dump(mode="json"), request)


@router.get("/{draft_public_id}/download")
def download_draft(draft_public_id: str, current_user: PapersReadDep):
    detail = get_shared_draft(draft_public_id, current_user)
    include_answer = bool(detail.state.get("includeAnswersInExport")) and has_permission(current_user, "answers:read")
    return _draft_docx_response(detail, include_answer=include_answer)
