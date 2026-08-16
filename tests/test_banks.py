from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from testpaper_backend.schemas import (
    BankAccessRole,
    BankCreate,
    BankForkRequest,
    BankItemAdd,
    BankMemberCreate,
    BankMemberUpdate,
    BankRole,
    BankSubscriptionUpdate,
    BankUpdate,
    UserEntity,
    UserRole,
)
from testpaper_backend.services import banks

UTC_DT = datetime(2026, 8, 5, tzinfo=UTC)


def _permissions(role: UserRole) -> list[str]:
    base = ["questions:read", "papers:read"]
    if role in {UserRole.admin, UserRole.teacher}:
        base.extend(["questions:write", "questions:delete", "answers:read", "papers:write"])
    if role == UserRole.admin:
        base.append("users:manage")
    if role in {UserRole.admin, UserRole.teacher}:
        base.extend(["banks:read", "banks:write", "banks:delete", "banks:publish", "banks:subscribe"])
    elif role == UserRole.viewer:
        base.extend(["banks:read", "banks:subscribe"])
    return base


def _user(user_id: int, role: UserRole = UserRole.teacher) -> UserEntity:
    return UserEntity(
        id=user_id,
        publicId=f"u-{user_id}",
        username=f"user{user_id}",
        displayName=f"User {user_id}",
        role=role,
        permissions=_permissions(role),
        isActive=True,
        createdAt=UTC_DT,
        updatedAt=UTC_DT,
    )


def _user_ref(user_id: int):
    return SimpleNamespace(public_id=f"u-{user_id}", username=f"user{user_id}", display_name=f"User {user_id}")


def _question_row(*, public_id: str = "q-1", question_type: str = "single_choice", answer: Any = "A"):
    return SimpleNamespace(
        id=int(public_id.split("-")[1]),
        public_id=public_id,
        type=question_type,
        subjects=["Math"],
        difficulty="medium",
        tags=["tag"],
        text=f"Question {public_id}",
        options=["A", "B"],
        answer=answer,
        has_latex=False,
        source=None,
        essay_blank_space=None,
        images=[],
        score_weight=1.0,
        owner_id=1,
        created_at=UTC_DT,
        updated_at=UTC_DT,
    )


def _item_row(*, question: SimpleNamespace, added_by: int = 1, created_at: Any = UTC_DT):
    return SimpleNamespace(question=question, question_id=question.id, added_by=added_by, created_at=created_at)


def _publication_row(
    *,
    version: int = 1,
    state: dict[str, Any] | None = None,
    created_by: int = 1,
    withdrawn_at: datetime | None = None,
):
    return SimpleNamespace(
        id=version,
        public_id=f"pub-{version}",
        bank_id=1,
        version=version,
        state=state or {"version": version, "visibility": "public", "publishedAt": "2026-08-05T00:00:00Z", "bank": {}, "items": []},
        created_by=created_by,
        created_by_user=_user_ref(created_by),
        created_at=UTC_DT,
        withdrawn_at=withdrawn_at,
    )


def _member_row(*, user_id: int, role: str = "viewer"):
    return SimpleNamespace(
        user_id=user_id,
        role=role,
        user=_user_ref(user_id),
        created_at=UTC_DT,
        updated_at=UTC_DT,
    )


def _subscription_row(*, user_id: int, publication: Any | None = None):
    return SimpleNamespace(
        bank_id=1,
        user_id=user_id,
        publication_id=publication.id if publication else None,
        publication=publication,
        created_at=UTC_DT,
        updated_at=UTC_DT,
    )


def _bank_row(
    *,
    owner_id: int = 1,
    visibility: str = "private",
    members: list[Any] | None = None,
    items: list[Any] | None = None,
    publications: list[Any] | None = None,
    subscriptions: list[Any] | None = None,
    latest_version: int = 0,
    name: str = "Bank",
):
    return SimpleNamespace(
        id=1,
        public_id="bank-1",
        name=name,
        description="",
        owner_id=owner_id,
        owner=_user_ref(owner_id),
        visibility=visibility,
        latest_version=latest_version,
        items=items or [],
        members=members or [],
        publications=publications or [],
        subscriptions=subscriptions or [],
        created_at=UTC_DT,
        updated_at=UTC_DT,
    )


class _ScalarResult:
    def __init__(self, rows: Any):
        self.rows = rows

    def first(self):
        if isinstance(self.rows, list):
            return self.rows[0] if self.rows else None
        return self.rows

    def all(self):
        return self.rows if isinstance(self.rows, list) else [self.rows] if self.rows is not None else []


class _FakeSession:
    def __init__(self, *results: Any):
        self.results = list(results)
        self.committed = False
        self.added: list[Any] = []
        self.deleted: list[Any] = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def scalars(self, _statement):
        if len(self.results) > 1:
            return _ScalarResult(self.results.pop(0))
        return _ScalarResult(self.results[0])

    def add(self, row):
        self.added.append(row)

    def flush(self):
        return None

    def delete(self, row):
        self.deleted.append(row)

    def commit(self):
        self.committed = True

    def expire_all(self):
        return None

    def refresh(self, row):
        return None

    def get(self, _model, _row_id):
        return None


def _patch_session(monkeypatch: pytest.MonkeyPatch, row: Any) -> _FakeSession:
    session = _FakeSession(row)
    monkeypatch.setattr(banks, "SessionLocal", lambda: session)
    return session


def test_bank_access_role_matrix() -> None:
    bank = _bank_row(owner_id=1, members=[_member_row(user_id=2, role="editor"), _member_row(user_id=3, role="viewer")])
    assert banks.bank_access_role(bank, _user(1)) == BankAccessRole.owner
    assert banks.bank_access_role(bank, _user(99, UserRole.admin)) == BankAccessRole.admin
    assert banks.bank_access_role(bank, _user(2)) == BankAccessRole.editor
    assert banks.bank_access_role(bank, _user(3, UserRole.viewer)) == BankAccessRole.viewer
    assert banks.bank_access_role(bank, _user(4, UserRole.viewer)) is None


def test_non_member_read_private_bank_is_404(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_session(monkeypatch, _bank_row(owner_id=1, visibility="private"))
    with pytest.raises(HTTPException) as exc_info:
        banks.get_bank_detail("bank-1", _user(4, UserRole.viewer))
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["code"] == "BANK_NOT_FOUND"


def test_published_public_bank_readable_by_any_authenticated_user(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_session(monkeypatch, _bank_row(owner_id=1, visibility="public", publications=[_publication_row()]))
    detail = banks.get_bank_detail("bank-1", _user(4, UserRole.viewer))
    assert detail.accessRole == BankAccessRole.viewer


def test_public_non_member_reads_snapshot_not_working_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    publication = _publication_row(
        state={
            "version": 1,
            "visibility": "public",
            "publishedAt": UTC_DT.isoformat(),
            "bank": {"publicId": "bank-1", "name": "Published name", "description": "Published description"},
            "items": [
                {
                    "publicId": "q-1",
                    "data": {
                        "type": "single_choice",
                        "subjects": ["Math"],
                        "difficulty": "medium",
                        "tags": [],
                        "text": "Published question",
                        "options": ["A", "B"],
                        "answer": "A",
                        "hasLatex": False,
                        "images": [],
                        "scoreWeight": 1.0,
                    },
                }
            ],
        }
    )
    live_question = _question_row(answer="UNPUBLISHED SECRET")
    live_question.text = "Unpublished working-copy question"
    row = _bank_row(
        owner_id=1,
        visibility="public",
        publications=[publication],
        items=[_item_row(question=live_question)],
    )
    _patch_session(monkeypatch, row)

    caller = _user(4, UserRole.teacher)
    detail = banks.get_bank_detail("bank-1", caller)
    questions = banks.list_bank_questions("bank-1", caller)

    assert detail.name == "Published name"
    assert detail.members == []
    assert [question.text for question in questions] == ["Published question"]
    assert questions[0].answer == "A"


def test_withdrawn_public_bank_hidden_from_non_member_but_pinned_subscription_survives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publication = _publication_row(version=1, withdrawn_at=UTC_DT)
    row = _bank_row(owner_id=1, visibility="public", publications=[publication])
    _patch_session(monkeypatch, row)

    with pytest.raises(HTTPException) as exc_info:
        banks.get_bank_detail("bank-1", _user(4, UserRole.viewer))
    assert exc_info.value.status_code == 404

    row.subscriptions = [_subscription_row(user_id=4, publication=publication)]
    detail = banks.get_bank_detail("bank-1", _user(4, UserRole.viewer))
    assert detail.version == 1
    assert detail.isSubscribed is True


def test_team_bank_hidden_from_non_member(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_session(monkeypatch, _bank_row(owner_id=1, visibility="team", members=[_member_row(user_id=2, role="editor")]))
    with pytest.raises(HTTPException) as exc_info:
        banks.get_bank_detail("bank-1", _user(4, UserRole.viewer))
    assert exc_info.value.status_code == 404
    assert banks.get_bank_detail("bank-1", _user(2)).accessRole == BankAccessRole.editor


def test_viewer_cannot_edit_items(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_session(monkeypatch, _bank_row(owner_id=1, members=[_member_row(user_id=2, role="viewer")]))
    with pytest.raises(HTTPException) as exc_info:
        banks.add_bank_items("bank-1", BankItemAdd(questionIds=["q-1"]), _user(2, UserRole.viewer))
    assert exc_info.value.status_code == 403


def test_editor_cannot_manage_members(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_session(monkeypatch, _bank_row(owner_id=1, members=[_member_row(user_id=2, role="editor")]))
    with pytest.raises(HTTPException) as exc_info:
        banks.add_bank_member("bank-1", BankMemberCreate(username="user5", role=BankRole.viewer), _user(2))
    assert exc_info.value.status_code == 403


def test_add_bank_items_duplicate_in_request_is_422(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_session(monkeypatch, _bank_row(owner_id=1))
    with pytest.raises(HTTPException) as exc_info:
        banks.add_bank_items("bank-1", BankItemAdd(questionIds=["q-1", "q-1"]), _user(1))
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["code"] == "VALIDATION_ERROR"


def test_add_bank_items_conflict_with_existing_is_409(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _bank_row(owner_id=1, items=[_item_row(question=_question_row(public_id="q-1"))])
    session = _FakeSession(row, _question_row(public_id="q-1"))
    monkeypatch.setattr(banks, "SessionLocal", lambda: session)
    with pytest.raises(HTTPException) as exc_info:
        banks.add_bank_items("bank-1", BankItemAdd(questionIds=["q-1"]), _user(1))
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "BANK_ITEM_EXISTS"
    assert exc_info.value.detail["details"]["questionPublicIds"] == ["q-1"]


def test_add_bank_items_missing_question_is_404(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession(_bank_row(owner_id=1), _question_row(public_id="q-1"))
    monkeypatch.setattr(banks, "SessionLocal", lambda: session)
    with pytest.raises(HTTPException) as exc_info:
        banks.add_bank_items("bank-1", BankItemAdd(questionIds=["q-99"]), _user(1))
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["code"] == "QUESTION_NOT_FOUND"


def test_owner_cannot_be_member_422(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _bank_row(owner_id=1)
    session = _FakeSession(row, SimpleNamespace(id=1, public_id="u-1", username="user1", display_name="User 1", is_active=True))
    monkeypatch.setattr(banks, "SessionLocal", lambda: session)
    with pytest.raises(HTTPException) as exc_info:
        banks.add_bank_member("bank-1", BankMemberCreate(username="user1", role=BankRole.editor), _user(1))
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["code"] == "BANK_OWNER_CANNOT_BE_MEMBER"


def test_member_already_exists_422(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _bank_row(owner_id=1, members=[_member_row(user_id=2, role="viewer")])
    session = _FakeSession(row, SimpleNamespace(id=2, public_id="u-2", username="user2", display_name="User 2", is_active=True))
    monkeypatch.setattr(banks, "SessionLocal", lambda: session)
    with pytest.raises(HTTPException) as exc_info:
        banks.add_bank_member("bank-1", BankMemberCreate(username="user2", role=BankRole.editor), _user(1))
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["code"] == "BANK_MEMBER_EXISTS"


def test_update_member_role_and_remove(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _bank_row(owner_id=1, members=[_member_row(user_id=2, role="viewer")])
    _patch_session(monkeypatch, row)
    detail = banks.update_bank_member_role("bank-1", "u-2", BankMemberUpdate(role=BankRole.editor), _user(1))
    assert detail.members[0].role == BankRole.editor

    session = _patch_session(monkeypatch, row)
    detail = banks.remove_bank_member("bank-1", "u-2", _user(1))
    assert session.deleted
    row.members = []
    assert banks._detail_from_row(row, _user(1)).memberCount == 0


def test_member_removed_loses_access() -> None:
    row = _bank_row(owner_id=1, visibility="team", members=[])
    assert banks.bank_access_role(row, _user(2)) is None
    with pytest.raises(HTTPException) as exc_info:
        banks._ensure_read_access(row, _user(2))
    assert exc_info.value.status_code == 404


def test_update_bank_visibility_requires_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _bank_row(owner_id=1, members=[_member_row(user_id=99, role="viewer")])
    _patch_session(monkeypatch, row)
    admin = _user(99, UserRole.admin)
    with pytest.raises(HTTPException) as exc_info:
        banks.update_bank("bank-1", BankUpdate(visibility="public"), admin)
    assert exc_info.value.status_code == 403


def test_publish_bank_empty_is_422(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_session(monkeypatch, _bank_row(owner_id=1))
    with pytest.raises(HTTPException) as exc_info:
        banks.publish_bank("bank-1", _user(1))
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["code"] == "BANK_PUBLISH_EMPTY"


def test_publish_bank_requires_banks_publish_permission(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _bank_row(owner_id=1, items=[_item_row(question=_question_row())])
    _patch_session(monkeypatch, row)
    viewer_owner = _user(1, UserRole.viewer)
    with pytest.raises(HTTPException) as exc_info:
        banks.publish_bank("bank-1", viewer_owner)
    assert exc_info.value.status_code == 403


def test_publish_bank_success_and_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _bank_row(owner_id=1, items=[_item_row(question=_question_row())])
    session = _patch_session(monkeypatch, row)
    banks.publish_bank("bank-1", _user(1))
    assert session.committed is True
    assert row.latest_version == 1
    assert session.added[0].version == 1
    assert session.added[0].state["items"][0]["data"]["answer"] == "A"
    row.publications = list(session.added)
    assert banks._detail_from_row(row, _user(1)).version == 1

    _patch_session(monkeypatch, row)
    with pytest.raises(HTTPException) as exc_info:
        banks.publish_bank("bank-1", _user(1))
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "BANK_ALREADY_PUBLISHED"


def test_withdraw_bank_not_published_409(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_session(monkeypatch, _bank_row(owner_id=1))
    with pytest.raises(HTTPException) as exc_info:
        banks.withdraw_bank("bank-1", _user(1))
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "BANK_NOT_PUBLISHED"


def test_withdraw_bank_retains_and_marks_latest_publication(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _bank_row(owner_id=1, publications=[_publication_row(version=1)])
    session = _patch_session(monkeypatch, row)
    banks.withdraw_bank("bank-1", _user(1))
    assert session.deleted == []
    assert row.publications[0].withdrawn_at is not None
    assert banks._detail_from_row(row, _user(1)).version is None


def test_publish_snapshot_immutable_after_bank_edit() -> None:
    row = _bank_row(owner_id=1, items=[_item_row(question=_question_row())])
    now = UTC_DT
    state = banks._build_snapshot_state(row, 1, now)
    row.items[0].question.answer = "CHANGED"
    assert state["items"][0]["data"]["answer"] == "A"


def test_redact_bank_snapshot_answers_placeholders() -> None:
    state = {
        "version": 1,
        "items": [
            {"publicId": "q-1", "data": {"type": "single_choice", "answer": "A"}},
            {"publicId": "q-2", "data": {"type": "multiple_choice", "answer": ["A", "B"]}},
        ],
    }
    redacted = banks.redact_bank_snapshot_answers(state, include_answers=False)
    assert redacted["items"][0]["data"]["answer"] == "[redacted]"
    assert redacted["items"][1]["data"]["answer"] == ["[redacted]"]
    assert state["items"][0]["data"]["answer"] == "A"

    kept = banks.redact_bank_snapshot_answers(state, include_answers=True)
    assert kept["items"][0]["data"]["answer"] == "A"


def test_get_bank_version_redacts_without_answers_read(monkeypatch: pytest.MonkeyPatch) -> None:
    state = {
        "version": 1,
        "visibility": "public",
        "publishedAt": "2026-08-05T00:00:00Z",
        "bank": {"publicId": "bank-1", "name": "Bank", "description": ""},
        "items": [{"publicId": "q-1", "data": {"type": "single_choice", "answer": "A"}}],
    }
    row = _bank_row(owner_id=1, visibility="public", publications=[_publication_row(version=1, state=state)])
    _patch_session(monkeypatch, row)

    redacted = banks.get_bank_version("bank-1", 1, _user(4, UserRole.viewer))
    assert redacted.state["items"][0]["data"]["answer"] == "[redacted]"

    full = banks.get_bank_version("bank-1", 1, _user(1))
    assert full.state["items"][0]["data"]["answer"] == "A"


def test_get_bank_version_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_session(monkeypatch, _bank_row(owner_id=1))
    with pytest.raises(HTTPException) as exc_info:
        banks.get_bank_version("bank-1", 5, _user(1))
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["code"] == "BANK_VERSION_NOT_FOUND"


def test_subscribe_private_bank_422(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_session(monkeypatch, _bank_row(owner_id=1, visibility="private"))
    with pytest.raises(HTTPException) as exc_info:
        banks.subscribe_bank("bank-1", _user(1))
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["code"] == "BANK_SUBSCRIBE_PRIVATE"


def test_subscribe_public_bank_success_and_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    publication = _publication_row(version=1)
    row = _bank_row(owner_id=1, visibility="public", publications=[publication])
    session = _patch_session(monkeypatch, row)
    subscription = banks.subscribe_bank("bank-1", _user(4, UserRole.viewer))
    assert session.committed is True
    assert subscription.userId == 4
    assert subscription.version == 1

    row.subscriptions = [_subscription_row(user_id=4, publication=publication)]
    banks.subscribe_bank("bank-1", _user(4, UserRole.viewer))
    assert len(session.added) == 1


def test_subscribe_requires_active_publication(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _bank_row(owner_id=1, visibility="public")
    _patch_session(monkeypatch, row)
    with pytest.raises(HTTPException) as exc_info:
        banks.subscribe_bank("bank-1", _user(4, UserRole.viewer))
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "BANK_NOT_PUBLISHED"


def test_update_subscription_advances_only_to_active_version(monkeypatch: pytest.MonkeyPatch) -> None:
    first = _publication_row(version=1, withdrawn_at=UTC_DT)
    second = _publication_row(version=2)
    subscription = _subscription_row(user_id=4, publication=first)
    row = _bank_row(
        owner_id=1,
        visibility="public",
        publications=[first, second],
        subscriptions=[subscription],
        latest_version=2,
    )
    session = _patch_session(monkeypatch, row)
    updated = banks.update_subscription("bank-1", BankSubscriptionUpdate(version=2), _user(4, UserRole.viewer))
    assert session.committed is True
    assert updated.version == 2
    assert subscription.publication_id == second.id

    _patch_session(monkeypatch, row)
    with pytest.raises(HTTPException) as exc_info:
        banks.update_subscription("bank-1", BankSubscriptionUpdate(version=1), _user(4, UserRole.viewer))
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "BANK_VERSION_NOT_ACTIVE"


def test_public_bank_detail_uses_active_snapshot_and_redacts_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    state = {
        "version": 1,
        "visibility": "public",
        "publishedAt": "2026-08-05T00:00:00Z",
        "bank": {"publicId": "bank-1", "name": "Published Bank", "description": "Stable"},
        "items": [{"publicId": "q-1", "data": {"type": "single_choice", "answer": "SECRET"}}],
    }
    row = _bank_row(owner_id=1, visibility="public", publications=[_publication_row(version=1, state=state)])
    _patch_session(monkeypatch, row)
    detail = banks.get_public_bank("bank-1")
    assert detail.name == "Published Bank"
    assert detail.itemCount == 1
    assert detail.state["items"][0]["data"]["answer"] == "[redacted]"


def test_public_bank_discovery_uses_only_public_snapshot_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    state = {
        "version": 1,
        "visibility": "public",
        "publishedAt": "2026-08-05T00:00:00Z",
        "bank": {"publicId": "bank-1", "name": "Published Algebra", "description": ""},
        "items": [],
    }
    row = _bank_row(owner_id=1, visibility="public", publications=[_publication_row(version=1, state=state)])
    row.name = "Unpublished Geometry Rename"
    row.description = "Mutable description"
    _patch_session(monkeypatch, [row])

    assert [bank.name for bank in banks.list_public_banks(q="algebra")] == ["Published Algebra"]
    assert banks.list_public_banks(q="geometry") == []
    summary = banks.list_public_banks()[0]
    assert summary.description == ""


def test_team_snapshot_cannot_become_anonymous_after_visibility_edit(monkeypatch: pytest.MonkeyPatch) -> None:
    state = {
        "version": 1,
        "visibility": "team",
        "publishedAt": "2026-08-05T00:00:00Z",
        "bank": {"publicId": "bank-1", "name": "Team Bank", "description": ""},
        "items": [],
    }
    row = _bank_row(owner_id=1, visibility="public", publications=[_publication_row(version=1, state=state)])
    _patch_session(monkeypatch, row)

    with pytest.raises(HTTPException) as exc_info:
        banks.get_public_bank("bank-1")
    assert exc_info.value.status_code == 404

    _patch_session(monkeypatch, [row])
    assert banks.list_public_banks() == []


def test_unsubscribe_removes_subscription(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _bank_row(owner_id=1, visibility="public", subscriptions=[_subscription_row(user_id=4)])
    session = _patch_session(monkeypatch, row)
    banks.unsubscribe_bank("bank-1", _user(4, UserRole.viewer))
    assert session.deleted[0].user_id == 4


def test_fork_creates_independent_private_bank(monkeypatch: pytest.MonkeyPatch) -> None:
    state = {
        "version": 1,
        "visibility": "public",
        "publishedAt": "2026-08-05T00:00:00Z",
        "bank": {"publicId": "bank-1", "name": "Bank", "description": ""},
        "items": [
            {
                "publicId": "q-1",
                "data": {
                    "type": "single_choice",
                    "subjects": ["Math"],
                    "difficulty": "medium",
                    "tags": [],
                    "text": "Copied question",
                    "options": ["A", "B"],
                    "answer": "A",
                    "hasLatex": False,
                    "scoreWeight": 1.0,
                    "images": [],
                },
            }
        ],
    }
    row = _bank_row(owner_id=1, visibility="public", publications=[_publication_row(version=1, state=state)])

    new_row = _bank_row(owner_id=4, visibility="private", name="Bank (fork)")
    session = _FakeSession(row)
    monkeypatch.setattr(banks, "SessionLocal", lambda: session)
    rows = iter([row, new_row])
    monkeypatch.setattr(banks, "_get_bank_row", lambda _session, _public_id: next(rows))

    detail = banks.fork_bank("bank-1", BankForkRequest(), _user(4, UserRole.viewer))
    assert session.committed is True
    assert detail.name == "Bank (fork)"
    assert detail.owner.username == "user4"
    created_questions = [item for item in session.added if type(item).__name__ == "QuestionRow"]
    assert len(created_questions) == 1
    assert created_questions[0].owner_id == 4
    assert created_questions[0].public_id != "q-1"


def test_fork_redacts_answers_without_answers_read(monkeypatch: pytest.MonkeyPatch) -> None:
    state = {
        "version": 1,
        "visibility": "public",
        "publishedAt": "2026-08-05T00:00:00Z",
        "bank": {"publicId": "bank-1", "name": "Bank", "description": ""},
        "items": [
            {
                "publicId": "q-1",
                "data": {
                    "type": "single_choice",
                    "subjects": ["Math"],
                    "difficulty": "medium",
                    "tags": [],
                    "text": "Copied question",
                    "options": ["A", "B"],
                    "answer": "SECRET",
                    "hasLatex": False,
                    "scoreWeight": 1.0,
                    "images": [],
                },
            }
        ],
    }
    row = _bank_row(owner_id=1, visibility="public", publications=[_publication_row(version=1, state=state)])
    new_row = _bank_row(owner_id=4, visibility="private", name="Bank (fork)")
    session = _FakeSession(row)
    monkeypatch.setattr(banks, "SessionLocal", lambda: session)
    rows = iter([row, new_row])
    monkeypatch.setattr(banks, "_get_bank_row", lambda _session, _public_id: next(rows))

    banks.fork_bank("bank-1", BankForkRequest(), _user(4, UserRole.viewer))
    created_questions = [item for item in session.added if type(item).__name__ == "QuestionRow"]
    assert created_questions[0].answer == "[redacted]"


def test_fork_requires_published_version(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_session(monkeypatch, _bank_row(owner_id=1, visibility="public"))
    with pytest.raises(HTTPException) as exc_info:
        banks.fork_bank("bank-1", BankForkRequest(), _user(4, UserRole.viewer))
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "BANK_NOT_PUBLISHED"


def test_create_bank_requires_banks_write_permission() -> None:
    with pytest.raises(HTTPException) as exc_info:
        banks.create_bank(BankCreate(name="New Bank"), _user(1, UserRole.viewer))
    assert exc_info.value.status_code == 403
