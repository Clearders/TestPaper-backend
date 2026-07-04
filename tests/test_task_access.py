from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from testpaper_backend.schemas import ROLE_PERMISSIONS, UserEntity, UserRole
from testpaper_backend.services import task_access


def _user(user_id: int, role: UserRole = UserRole.teacher) -> UserEntity:
    now = datetime(2026, 6, 14, tzinfo=UTC)
    return UserEntity(
        id=user_id,
        publicId=f"user-{user_id}",
        username=f"user{user_id}",
        displayName=f"User {user_id}",
        role=role,
        permissions=sorted(ROLE_PERMISSIONS[role]),
        isActive=True,
        createdAt=now,
        updatedAt=now,
    )


def test_dispatch_owned_task_cleans_owner_key_when_celery_dispatch_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    values: dict[str, str] = {}
    fake_redis = SimpleNamespace(
        setex=lambda key, ttl, value: values.__setitem__(key, value),
        delete=lambda key: values.pop(key, None),
    )

    def fail_send_task(name: str, args: list[object], kwargs: dict[str, object], task_id: str) -> object:
        raise RuntimeError("broker down")

    monkeypatch.setattr(task_access, "get_redis", lambda: fake_redis)
    monkeypatch.setattr(task_access.celery, "send_task", fail_send_task)

    with pytest.raises(Exception) as exc_info:
        task_access.dispatch_owned_task("ping", _user(4))

    assert getattr(exc_info.value, "status_code", None) == 503
    assert exc_info.value.detail["code"] == "TASK_QUEUE_UNAVAILABLE"
    assert values == {}


def test_dispatch_owned_task_preserves_queue_error_when_owner_cleanup_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_redis = SimpleNamespace(
        setex=lambda key, ttl, value: None,
        delete=lambda key: (_ for _ in ()).throw(ConnectionError("redis cleanup failed")),
    )

    def fail_send_task(name: str, args: list[object], kwargs: dict[str, object], task_id: str) -> object:
        raise RuntimeError("broker down")

    monkeypatch.setattr(task_access, "get_redis", lambda: fake_redis)
    monkeypatch.setattr(task_access.celery, "send_task", fail_send_task)

    with pytest.raises(Exception) as exc_info:
        task_access.dispatch_owned_task("ping", _user(4))

    assert getattr(exc_info.value, "status_code", None) == 503
    assert exc_info.value.detail["code"] == "TASK_QUEUE_UNAVAILABLE"
    assert isinstance(exc_info.value.__cause__, RuntimeError)


def test_admin_task_access_does_not_require_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        task_access,
        "get_redis",
        lambda: (_ for _ in ()).throw(ConnectionError("redis unavailable")),
    )

    task_access.ensure_task_access("task-1", _user(1, UserRole.admin))


def test_dispatched_task_payload_merges_extra_identifiers() -> None:
    payload = task_access.dispatched_task_payload(SimpleNamespace(id="task-1"), paperId="paper-1")

    assert payload == {"taskId": "task-1", "status": "dispatched", "paperId": "paper-1"}


def test_task_status_payload_serializes_success(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeAsyncResult:
        def __init__(self, task_id: str, app: object) -> None:
            self.task_id = task_id
            self.app = app
            self.state = "SUCCESS"
            self.result = {"ok": True}
            self.info = None

    monkeypatch.setattr(task_access, "AsyncResult", FakeAsyncResult)

    assert task_access.task_status_payload("task-1") == {
        "taskId": "task-1",
        "status": "SUCCESS",
        "result": {"ok": True},
    }


def test_task_status_payload_serializes_failure_without_info(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeAsyncResult:
        def __init__(self, task_id: str, app: object) -> None:
            self.task_id = task_id
            self.app = app
            self.state = "FAILURE"
            self.result = None
            self.info = None

    monkeypatch.setattr(task_access, "AsyncResult", FakeAsyncResult)

    assert task_access.task_status_payload("task-1") == {
        "taskId": "task-1",
        "status": "FAILURE",
        "error": "Unknown error",
    }
