from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.requests import Request

from testpaper_backend.config import get_cors_origins, get_trusted_hosts
from testpaper_backend.core.csrf import CSRFMiddleware
from testpaper_backend.core.factory import create_app
from testpaper_backend.core.lifespan import lifespan
from testpaper_backend.schemas import (
    ROLE_PERMISSIONS,
    CorrectionCategory,
    CorrectionStatus,
    Difficulty,
    PaperGenerateRequest,
    PasswordChange,
    ProfileUpdate,
    QuestionCorrectionCreate,
    QuestionCreate,
    QuestionType,
    QuestionUpdate,
    RegisterRequest,
    UserEntity,
    UserRole,
    UserUpdate,
)
from testpaper_backend.security import password_hash
from testpaper_backend.services import rate_limit, task_access
from testpaper_backend.services.profiles import change_user_password, update_user_profile
from testpaper_backend.services.questions import (
    ensure_question_correction_access,
    ensure_question_owner_access,
    normalize_update_owner,
    update_correction_status,
)
from testpaper_backend.services.rate_limit import get_client_ip
from testpaper_backend.services.realtime import get_websocket_token
from testpaper_backend.services.users import delete_managed_user, update_managed_user
from testpaper_backend.worker.tasks import _to_csv_format


def _user(user_id: int, role: UserRole) -> UserEntity:
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


def test_trimmed_required_fields_cannot_be_empty() -> None:
    with pytest.raises(ValidationError):
        RegisterRequest(username="   ", displayName="Name", password="password1")
    with pytest.raises(ValidationError):
        QuestionCorrectionCreate(category=CorrectionCategory.typo, message="   ")
    with pytest.raises(ValidationError):
        QuestionCreate(
            type=QuestionType.single_choice,
            subjects=["Math"],
            difficulty=Difficulty.easy,
            text="Question",
            options=["   "],
            answer="A",
        )
    with pytest.raises(ValidationError):
        UserUpdate(isActive=None)


def test_question_normalization_is_consistent() -> None:
    question = QuestionCreate(
        type=QuestionType.multiple_choice,
        subjects=[" Mathematics ", "Mathematics"],
        difficulty=Difficulty.medium,
        tags=[" Algebra ", "algebra"],
        text="  Select all  ",
        options=[" A ", "B"],
        answer=[" A ", "A"],
    )
    assert question.subjects == ["Mathematics"]
    assert question.tags == ["algebra"]
    assert question.text == "Select all"
    assert question.answer == ["A"]


def test_generation_rejects_subjects_that_normalize_to_empty() -> None:
    with pytest.raises(ValidationError):
        PaperGenerateRequest(
            title="Paper",
            duration=60,
            totalMarks=100,
            difficultyCoefficient=0.5,
            questionTypes=[{"questionType": "single_choice", "count": 1}],
            subjects=["   "],
        )


def test_rate_limit_ip_uses_trusted_request_client() -> None:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"x-forwarded-for", b"203.0.113.99")],
            "client": ("127.0.0.1", 12345),
            "server": ("test", 80),
            "scheme": "http",
            "query_string": b"",
        }
    )
    assert get_client_ip(request) == "127.0.0.1"


def test_rate_limit_uses_local_fallback_when_redis_is_down(monkeypatch) -> None:
    rate_limit._fallback_counters.clear()
    monkeypatch.setattr(rate_limit, "get_redis", lambda: (_ for _ in ()).throw(ConnectionError("down")))
    rate_limit.check_rate_limit("login:test", 1, 60)
    with pytest.raises(Exception) as exc_info:
        rate_limit.check_rate_limit("login:test", 1, 60)
    assert getattr(exc_info.value, "status_code", None) == 429


def test_websocket_token_is_not_accepted_from_query_string() -> None:
    websocket = SimpleNamespace(
        headers={},
        cookies={},
        query_params={"token": "leaky-token"},
    )
    assert get_websocket_token(websocket) is None


def test_production_security_configuration_is_fail_closed(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "true")
    monkeypatch.setenv("CORS_ORIGINS", "*")
    monkeypatch.setenv("TRUSTED_HOSTS", "*")
    with pytest.raises(RuntimeError):
        get_cors_origins()
    with pytest.raises(RuntimeError):
        get_trusted_hosts()

    monkeypatch.setenv("CORS_ORIGINS", "https://testpapers.example.com")
    monkeypatch.setenv("TRUSTED_HOSTS", "testpapers.example.com")
    production_app = create_app(lifespan=lifespan)
    assert production_app.docs_url is None
    assert production_app.openapi_url is None


def test_bearer_requests_do_not_require_cookie_csrf() -> None:
    csrf_app = FastAPI()
    csrf_app.add_middleware(CSRFMiddleware)

    @csrf_app.post("/write")
    def write_endpoint():
        return {"ok": True}

    client = TestClient(csrf_app)
    cookie_response = client.post("/write")
    assert cookie_response.status_code == 403
    assert cookie_response.json()["error"]["code"] == "CSRF_MISSING"

    bearer_response = client.post(
        "/write",
        headers={"Authorization": "Bearer invalid-token"},
    )
    assert bearer_response.status_code == 200
    assert bearer_response.json() == {"ok": True}


def test_hsts_requires_secure_production_cookie(monkeypatch) -> None:
    from testpaper_backend.application import app

    client = TestClient(app)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    assert "strict-transport-security" not in client.get("/").headers

    monkeypatch.setenv("AUTH_COOKIE_SECURE", "true")
    assert client.get("/").headers["strict-transport-security"].startswith("max-age=")


def test_admin_update_preserves_owner_unless_explicit(monkeypatch) -> None:
    admin = _user(1, UserRole.admin)
    omitted = QuestionUpdate(text="Updated")
    normalize_update_owner(omitted, admin)
    assert "ownerId" not in omitted.model_dump(exclude_unset=True)

    explicit = QuestionUpdate(ownerId=7)
    monkeypatch.setattr(
        "testpaper_backend.services.questions.normalize_question_owner",
        lambda owner_id, current_user: owner_id,
    )
    normalize_update_owner(explicit, admin)
    assert explicit.ownerId == 7


def test_teacher_update_claims_legacy_question() -> None:
    teacher = _user(9, UserRole.teacher)
    payload = QuestionUpdate(text="Updated")
    normalize_update_owner(payload, teacher)
    assert payload.ownerId == teacher.id


def test_teacher_can_delete_owned_questions() -> None:
    assert "questions:delete" in ROLE_PERMISSIONS[UserRole.teacher]
    assert "users:manage" not in ROLE_PERMISSIONS[UserRole.teacher]

    teacher = _user(9, UserRole.teacher)
    ensure_question_owner_access(SimpleNamespace(ownerId=teacher.id), teacher)
    with pytest.raises(Exception) as exc_info:
        ensure_question_owner_access(SimpleNamespace(ownerId=teacher.id + 1), teacher)
    assert getattr(exc_info.value, "status_code", None) == 403


def test_csv_export_neutralizes_spreadsheet_formulas() -> None:
    exported = _to_csv_format(
        {
            "questions": [
                {
                    "type": "short_answer",
                    "subjects": ["Math"],
                    "difficulty": "easy",
                    "text": '=HYPERLINK("https://example.invalid")',
                    "answer": "+1+1",
                    "marks": 1,
                }
            ],
            "exportedAt": "now",
        }
    )["csv"]
    assert "'=HYPERLINK" in exported
    assert "'+1+1" in exported


def test_update_missing_correction_returns_404(monkeypatch) -> None:
    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def get(self, row_type, row_id):
            return None

    monkeypatch.setattr("testpaper_backend.services.questions.SessionLocal", FakeSession)
    with pytest.raises(Exception) as exc_info:
        update_correction_status(404, 1, CorrectionStatus.accepted)
    assert getattr(exc_info.value, "status_code", None) == 404


def test_update_correction_for_other_question_returns_404(monkeypatch) -> None:
    row = SimpleNamespace(question_id=99)

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def get(self, row_type, row_id):
            return row

    monkeypatch.setattr("testpaper_backend.services.questions.SessionLocal", FakeSession)
    with pytest.raises(Exception) as exc_info:
        update_correction_status(404, 1, CorrectionStatus.accepted)
    assert getattr(exc_info.value, "status_code", None) == 404


def test_teacher_cannot_manage_other_question_corrections() -> None:
    teacher = _user(9, UserRole.teacher)
    with pytest.raises(Exception) as exc_info:
        ensure_question_correction_access(SimpleNamespace(ownerId=teacher.id + 1), teacher)
    assert getattr(exc_info.value, "status_code", None) == 403


def test_recent_username_change_is_rejected_in_profile_service(monkeypatch) -> None:
    now = datetime(2026, 6, 14, tzinfo=UTC)
    row = SimpleNamespace(
        id=7,
        username="teacher",
        last_username_changed_at=now,
    )

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def get(self, row_type, row_id):
            return row

    monkeypatch.setattr("testpaper_backend.services.profiles.SessionLocal", FakeSession)
    monkeypatch.setattr("testpaper_backend.services.profiles.now_utc", lambda: now)

    with pytest.raises(Exception) as exc_info:
        update_user_profile(7, ProfileUpdate(username="teacher-renamed"))

    assert getattr(exc_info.value, "status_code", None) == 400
    assert exc_info.value.detail["code"] == "USERNAME_CHANGE_TOO_SOON"


def test_password_change_revokes_other_sessions_but_keeps_current(monkeypatch) -> None:
    current_token = "current-token"
    executed = []
    row = SimpleNamespace(password_hash=password_hash("Oldpass1"), updated_at=None)

    class FakeDelete:
        def __init__(self):
            self.where_calls = 0

        def where(self, *_conditions):
            self.where_calls += 1
            return self

    delete_statement = FakeDelete()

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def get(self, row_type, row_id):
            return row

        def execute(self, statement):
            executed.append(statement)

        def commit(self):
            pass

    monkeypatch.setattr("testpaper_backend.services.profiles.SessionLocal", FakeSession)
    monkeypatch.setattr("testpaper_backend.services.profiles.delete", lambda _row_type: delete_statement)

    change_user_password(7, PasswordChange(currentPassword="Oldpass1", newPassword="Newpass1"), current_token)

    assert executed == [delete_statement]
    assert delete_statement.where_calls == 2


def test_admin_cannot_change_own_role_in_user_service(monkeypatch) -> None:
    current_user = _user(7, UserRole.admin)
    row = SimpleNamespace(id=current_user.id, public_id=current_user.publicId)

    class FakeScalarResult:
        def first(self):
            return row

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def scalars(self, _statement):
            return FakeScalarResult()

    monkeypatch.setattr("testpaper_backend.services.users.SessionLocal", FakeSession)

    with pytest.raises(Exception) as exc_info:
        update_managed_user(current_user.publicId, UserUpdate(role=UserRole.teacher), current_user)

    assert getattr(exc_info.value, "status_code", None) == 422
    assert exc_info.value.detail["code"] == "SELF_MODIFICATION_FORBIDDEN"


def test_admin_cannot_delete_own_account_in_user_service() -> None:
    current_user = _user(7, UserRole.admin)

    with pytest.raises(Exception) as exc_info:
        delete_managed_user(current_user.publicId, current_user)

    assert getattr(exc_info.value, "status_code", None) == 422
    assert exc_info.value.detail["code"] == "VALIDATION_ERROR"


def test_task_results_are_bound_to_dispatching_user(monkeypatch) -> None:
    values: dict[str, str] = {}
    fake_redis = SimpleNamespace(
        setex=lambda key, ttl, value: values.__setitem__(key, value),
        get=values.get,
        delete=lambda key: values.pop(key, None),
    )
    monkeypatch.setattr(task_access, "get_redis", lambda: fake_redis)
    monkeypatch.setattr(
        task_access.celery,
        "send_task",
        lambda name, args, kwargs, task_id: SimpleNamespace(id=task_id),
    )

    owner = _user(4, UserRole.teacher)
    other = _user(5, UserRole.teacher)
    result = task_access.dispatch_owned_task("ping", owner)
    task_access.ensure_task_access(result.id, owner)
    with pytest.raises(Exception) as exc_info:
        task_access.ensure_task_access(result.id, other)
    assert getattr(exc_info.value, "status_code", None) == 404
