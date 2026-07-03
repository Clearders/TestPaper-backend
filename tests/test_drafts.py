from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from testpaper_backend.schemas import DraftAccessRole, DraftReviewStatus, PaperDraftCommentUpdate, PaperDraftUpdate, UserEntity, UserRole
from testpaper_backend.services import drafts


def _user(user_id: int, role: UserRole = UserRole.teacher) -> UserEntity:
    permissions = ["questions:read", "papers:read"]
    if role in {UserRole.admin, UserRole.teacher}:
        permissions.extend(["questions:write", "questions:delete", "answers:read", "papers:write"])
    if role == UserRole.admin:
        permissions.append("users:manage")
    return UserEntity(
        id=user_id,
        publicId=f"u-{user_id}",
        username=f"user{user_id}",
        displayName=f"User {user_id}",
        role=role,
        permissions=permissions,
        isActive=True,
        createdAt=datetime(2026, 7, 2, tzinfo=UTC),
        updatedAt=datetime(2026, 7, 2, tzinfo=UTC),
    )


def _user_row(user_id: int):
    return SimpleNamespace(public_id=f"u-{user_id}", username=f"user{user_id}", display_name=f"User {user_id}")


def _draft_row(*, owner_id: int = 1, revision: int = 3, collaborator_role: str | None = None, collaborator_id: int = 2):
    collaborators = []
    if collaborator_role is not None:
        collaborators.append(
            SimpleNamespace(
                user_id=collaborator_id,
                role=collaborator_role,
                user=_user_row(collaborator_id),
                created_at=datetime(2026, 7, 2, tzinfo=UTC),
                updated_at=datetime(2026, 7, 2, tzinfo=UTC),
            )
        )
    return SimpleNamespace(
        id=10,
        public_id="draft-1",
        name="Shared Draft",
        owner_id=owner_id,
        owner=_user_row(owner_id),
        state={"paper": {"title": "Draft", "subject": "Math", "duration": 60, "totalMarks": 100, "questions": []}},
        review_status=DraftReviewStatus.draft.value,
        revision=revision,
        updated_by=owner_id,
        updated_by_user=_user_row(owner_id),
        collaborators=collaborators,
        comments=[],
        created_at=datetime(2026, 7, 2, tzinfo=UTC),
        updated_at=datetime(2026, 7, 2, tzinfo=UTC),
    )


class _ScalarResult:
    def __init__(self, row):
        self.row = row

    def first(self):
        return self.row


class _FakeSession:
    def __init__(self, row):
        self.row = row
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def scalars(self, statement):
        return _ScalarResult(self.row)

    def commit(self):
        self.committed = True

    def refresh(self, row):
        return None


def _patch_session(monkeypatch: pytest.MonkeyPatch, row):
    session = _FakeSession(row)
    monkeypatch.setattr(drafts, "SessionLocal", lambda: session)
    return session


def test_draft_access_role_owner_admin_editor_viewer_and_none() -> None:
    owner_row = _draft_row(owner_id=1)
    editor_row = _draft_row(owner_id=1, collaborator_role="editor", collaborator_id=2)
    viewer_row = _draft_row(owner_id=1, collaborator_role="viewer", collaborator_id=3)

    assert drafts.draft_access_role(owner_row, _user(1)) == DraftAccessRole.owner
    assert drafts.draft_access_role(owner_row, _user(99, UserRole.admin)) == DraftAccessRole.admin
    assert drafts.draft_access_role(editor_row, _user(2)) == DraftAccessRole.editor
    assert drafts.draft_access_role(viewer_row, _user(3, UserRole.viewer)) == DraftAccessRole.viewer
    assert drafts.draft_access_role(owner_row, _user(4, UserRole.viewer)) is None


def test_redact_draft_state_answers_removes_nested_original_answers() -> None:
    state = {
        "paper": {
            "questions": [
                {"text": "Q1", "answer": "A", "originalQuestion": {"answer": "B"}},
                {"text": "Q2", "answer": ["A", "C"]},
            ]
        }
    }

    redacted = drafts.redact_draft_state_answers(state, include_answers=False)

    assert "answer" not in redacted["paper"]["questions"][0]
    assert "answer" not in redacted["paper"]["questions"][0]["originalQuestion"]
    assert "answer" not in redacted["paper"]["questions"][1]
    assert state["paper"]["questions"][0]["answer"] == "A"


def test_update_shared_draft_rejects_stale_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _draft_row(owner_id=1, revision=3)
    _patch_session(monkeypatch, row)

    with pytest.raises(HTTPException) as exc_info:
        drafts.update_shared_draft(
            "draft-1",
            PaperDraftUpdate(baseRevision=2, state={"paper": {"title": "New", "questions": []}}),
            _user(1),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "DRAFT_REVISION_CONFLICT"


def test_editor_can_request_review_but_cannot_approve(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _draft_row(owner_id=1, revision=3, collaborator_role="editor", collaborator_id=2)
    session = _patch_session(monkeypatch, row)

    detail = drafts.update_shared_draft(
        "draft-1",
        PaperDraftUpdate(baseRevision=3, reviewStatus=DraftReviewStatus.in_review),
        _user(2),
    )

    assert session.committed is True
    assert detail.reviewStatus == DraftReviewStatus.in_review
    assert row.revision == 4

    with pytest.raises(HTTPException) as exc_info:
        drafts.update_shared_draft(
            "draft-1",
            PaperDraftUpdate(baseRevision=4, reviewStatus=DraftReviewStatus.approved),
            _user(2),
        )

    assert exc_info.value.status_code == 403


def test_owner_cannot_approve_draft_with_open_comments(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _draft_row(owner_id=1, revision=3)
    row.comments = [
        SimpleNamespace(
            id=5,
            public_id="comment-1",
            question_public_id=None,
            message="Needs review",
            status="open",
            author_id=2,
            author=_user_row(2),
            created_at=datetime(2026, 7, 2, tzinfo=UTC),
            updated_at=datetime(2026, 7, 2, tzinfo=UTC),
        )
    ]
    _patch_session(monkeypatch, row)

    with pytest.raises(HTTPException) as exc_info:
        drafts.update_shared_draft(
            "draft-1",
            PaperDraftUpdate(baseRevision=3, reviewStatus=DraftReviewStatus.approved),
            _user(1),
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["code"] == "DRAFT_OPEN_COMMENTS"
    assert exc_info.value.detail["openCommentCount"] == 1


def test_editor_can_update_state_when_name_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _draft_row(owner_id=1, revision=3, collaborator_role="editor", collaborator_id=2)
    _patch_session(monkeypatch, row)

    detail = drafts.update_shared_draft(
        "draft-1",
        PaperDraftUpdate(
            baseRevision=3,
            name=row.name,
            state={"paper": {"title": "Editor Update", "questions": []}},
        ),
        _user(2),
    )

    assert detail.revision == 4
    assert row.state["paper"]["title"] == "Editor Update"


def test_viewer_cannot_edit_draft_state(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _draft_row(owner_id=1, revision=3, collaborator_role="viewer", collaborator_id=2)
    _patch_session(monkeypatch, row)

    with pytest.raises(HTTPException) as exc_info:
        drafts.update_shared_draft(
            "draft-1",
            PaperDraftUpdate(baseRevision=3, state={"paper": {"title": "Nope", "questions": []}}),
            _user(2, UserRole.viewer),
        )

    assert exc_info.value.status_code == 403


def test_comment_update_allows_author_but_blocks_other_viewer(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _draft_row(owner_id=1, revision=3, collaborator_role="viewer", collaborator_id=2)
    comment = SimpleNamespace(
        id=5,
        public_id="comment-1",
        question_public_id=None,
        message="Needs review",
        status="open",
        author_id=2,
        author=_user_row(2),
        created_at=datetime(2026, 7, 2, tzinfo=UTC),
        updated_at=datetime(2026, 7, 2, tzinfo=UTC),
    )
    row.comments = [comment]
    _patch_session(monkeypatch, row)

    detail = drafts.update_draft_comment(
        "draft-1",
        "comment-1",
        PaperDraftCommentUpdate(status="resolved"),
        _user(2, UserRole.viewer),
    )

    assert detail.comments[0].status == "resolved"

    row.collaborators = [
        SimpleNamespace(
            user_id=3,
            role="viewer",
            user=_user_row(3),
            created_at=datetime(2026, 7, 2, tzinfo=UTC),
            updated_at=datetime(2026, 7, 2, tzinfo=UTC),
        )
    ]
    with pytest.raises(HTTPException) as exc_info:
        drafts.update_draft_comment(
            "draft-1",
            "comment-1",
            PaperDraftCommentUpdate(message="Changed by someone else"),
            _user(3, UserRole.viewer),
        )

    assert exc_info.value.status_code == 403
