from __future__ import annotations

import zipfile
from datetime import UTC, datetime, timedelta
from io import BytesIO
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from fastapi import FastAPI, HTTPException, Response
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.requests import Request

from testpaper_backend.api.routes import auth as auth_routes
from testpaper_backend.api.routes import drafts as draft_routes
from testpaper_backend.api.routes import papers as paper_routes
from testpaper_backend.api.routes import questions as question_routes
from testpaper_backend.config import get_cors_origins, get_trusted_hosts
from testpaper_backend.core.csrf import CSRFMiddleware, set_csrf_cookie
from testpaper_backend.core.factory import create_app
from testpaper_backend.core.lifespan import lifespan
from testpaper_backend.documents.paper_docx import DOCX_MEDIA_TYPE
from testpaper_backend.repositories import question_row_to_entity
from testpaper_backend.schemas import (
    ROLE_PERMISSIONS,
    AuthSession,
    CorrectionCategory,
    CorrectionStatus,
    Difficulty,
    DraftAccessRole,
    DraftReviewStatus,
    PaperDraftDetail,
    PaperEntity,
    PaperGenerateRequest,
    PasswordChange,
    ProfileUpdate,
    QuestionCorrectionCreate,
    QuestionCorrectionUpdate,
    QuestionCreate,
    QuestionRef,
    QuestionRevisionEntity,
    QuestionType,
    QuestionUpdate,
    RegisterRequest,
    UserEntity,
    UserRole,
    UserUpdate,
)
from testpaper_backend.security import get_current_user, password_hash
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


def _auth_session(expires_at: datetime | None = None) -> AuthSession:
    return AuthSession(
        expiresAt=expires_at or datetime(2026, 6, 15, tzinfo=UTC),
        user=_user(1, UserRole.viewer),
    )


def _request(path: str = "/api/v1/questions/question-1/corrections/1") -> Request:
    request = Request(
        {
            "type": "http",
            "method": "PATCH",
            "path": path,
            "headers": [],
            "client": ("testclient", 50000),
            "server": ("test", 80),
            "scheme": "http",
            "query_string": b"",
        }
    )
    request.state.request_id = "test-request"
    return request


def _draft_choice_question(index: int) -> dict[str, object]:
    return {
        "questionPublicId": f"question-{index}",
        "orderNo": index,
        "marks": 10,
        "type": "single_choice",
        "subjects": ["Math"],
        "difficulty": "medium",
        "tags": ["edited"],
        "text": f"Auto density question {index}.",
        "options": ["A", "B", "C", "D"],
        "answer": f"Answer {index}",
        "hasLatex": False,
        "scoreWeight": 1,
    }


def _draft_detail(
    *, state: dict, name: str = "Cloud Draft Export", access_role: DraftAccessRole = DraftAccessRole.owner
) -> PaperDraftDetail:
    now = datetime(2026, 7, 2, tzinfo=UTC)
    return PaperDraftDetail(
        id=99,
        publicId="draft-cloud",
        name=name,
        owner=None,
        accessRole=access_role,
        reviewStatus=DraftReviewStatus.draft,
        revision=3,
        collaboratorCount=0,
        commentCount=0,
        openCommentCount=0,
        updatedBy=None,
        createdAt=now,
        updatedAt=now,
        state=state,
        collaborators=[],
        comments=[],
    )


def _assert_docx_download_contract(response, expected_filename: str, expected_layout_density: str) -> None:
    encoded_filename = quote(expected_filename)
    assert response.headers["content-type"] == DOCX_MEDIA_TYPE
    assert response.headers["content-disposition"] == (f"attachment; filename=\"{expected_filename}\"; filename*=UTF-8''{encoded_filename}")
    assert response.headers["x-export-format"] == "docx"
    assert response.headers["x-layout-density"] == expected_layout_density


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


def test_set_csrf_cookie_uses_auth_session_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)
    expires_at = now + timedelta(hours=2)
    monkeypatch.setattr("testpaper_backend.core.csrf.now_utc", lambda: now)

    response = Response()
    set_csrf_cookie(response, "csrf-token", expires_at)

    cookie_header = response.headers["set-cookie"].lower()
    assert "max-age=7200" in cookie_header
    assert "expires=" in cookie_header


def test_auth_routes_refresh_csrf_cookie_with_auth_session_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    expires_at = datetime(2026, 6, 15, tzinfo=UTC)
    session = _auth_session(expires_at)
    auth_cookie_calls: list[tuple[str, datetime]] = []
    csrf_cookie_calls: list[tuple[str, datetime]] = []

    def fake_set_auth_cookie(response: Response, token: str, cookie_expires_at: datetime) -> None:
        auth_cookie_calls.append((token, cookie_expires_at))

    def fake_set_csrf_cookie(response: Response, token: str, cookie_expires_at: datetime) -> None:
        csrf_cookie_calls.append((token, cookie_expires_at))

    monkeypatch.setattr(auth_routes, "authenticate_user", lambda payload: ("login-token", session))
    monkeypatch.setattr(auth_routes, "register_user", lambda payload: ("register-token", session))
    monkeypatch.setattr(auth_routes, "refresh_auth_session", lambda token: ("refresh-token", session))
    monkeypatch.setattr(auth_routes, "get_request_token", lambda request: "old-token")
    monkeypatch.setattr(auth_routes, "generate_csrf_token", lambda: "csrf-token")
    monkeypatch.setattr(auth_routes, "set_auth_cookie", fake_set_auth_cookie)
    monkeypatch.setattr(auth_routes, "set_csrf_cookie", fake_set_csrf_cookie)

    auth_routes.login(_request("/api/v1/auth/login"), Response(), SimpleNamespace(), None)
    auth_routes.register(_request("/api/v1/auth/register"), Response(), SimpleNamespace(), None)
    auth_routes.refresh_session(_request("/api/v1/auth/refresh"), Response())

    assert auth_cookie_calls == [
        ("login-token", expires_at),
        ("register-token", expires_at),
        ("refresh-token", expires_at),
    ]
    assert csrf_cookie_calls == [
        ("csrf-token", expires_at),
        ("csrf-token", expires_at),
        ("csrf-token", expires_at),
    ]


def test_question_images_require_backend_uploaded_png_paths() -> None:
    image_path = f"/api/v1/images/files/{'a' * 32}.png"
    question = QuestionCreate(
        type=QuestionType.single_choice,
        subjects=["Math"],
        difficulty=Difficulty.easy,
        text="Question",
        options=["A", "B"],
        answer="A",
        images=[{"url": image_path, "caption": "Figure 1"}],
    )
    assert question.images[0].url == image_path

    update = QuestionUpdate(images=[{"url": f"https://example.test{image_path}?cache=1"}])
    assert update.images is not None
    assert update.images[0].url == image_path

    for bad_url in (
        "data:image/png;base64,AAAA",
        "https://example.test/uploads/question.png",
        "/api/v1/images/files/not-a-question-image.png",
    ):
        with pytest.raises(ValidationError):
            QuestionCreate(
                type=QuestionType.single_choice,
                subjects=["Math"],
                difficulty=Difficulty.easy,
                text="Question",
                options=["A", "B"],
                answer="A",
                images=[{"url": bad_url}],
            )
        with pytest.raises(ValidationError):
            QuestionUpdate(images=[{"url": bad_url}])


def test_question_row_to_entity_filters_invalid_legacy_question_images() -> None:
    now = datetime(2026, 6, 14, tzinfo=UTC)
    image_path = f"/api/v1/images/files/{'b' * 32}.png"
    row = SimpleNamespace(
        id=1,
        public_id="question-1",
        type=QuestionType.single_choice.value,
        subjects=["Math"],
        difficulty=Difficulty.easy.value,
        tags=[],
        text="Question",
        options=["A", "B"],
        answer="A",
        has_latex=False,
        source=None,
        essay_blank_space=None,
        images=[
            {"url": "data:image/png;base64,AAAA", "caption": "Legacy inline"},
            {"url": "https://example.test/uploads/question.png", "caption": "External"},
            {"url": image_path, "caption": "Uploaded"},
        ],
        score_weight=1.0,
        owner_id=1,
        created_at=now,
        updated_at=now,
    )

    entity = question_row_to_entity(row)

    assert [(image.url, image.caption) for image in entity.images] == [(image_path, "Uploaded")]


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


def test_delete_cloud_draft_broadcasts_deleted_event(monkeypatch) -> None:
    teacher = _user(18, UserRole.teacher)
    delete_calls = []
    background_calls = []
    broadcast = object()

    class FakeBackgroundTasks:
        def add_task(self, func, *args, **kwargs):
            background_calls.append((func, args, kwargs))

    monkeypatch.setattr(
        draft_routes,
        "delete_shared_draft",
        lambda draft_public_id, current_user: delete_calls.append((draft_public_id, current_user.id)),
    )
    monkeypatch.setattr(draft_routes, "realtime", SimpleNamespace(broadcast=broadcast))

    response = draft_routes.delete_draft(FakeBackgroundTasks(), "draft-cloud", teacher, None)

    assert response.status_code == 204
    assert delete_calls == [("draft-cloud", teacher.id)]
    assert background_calls == [
        (
            broadcast,
            (
                "draft.deleted",
                {
                    "draftId": "draft-cloud",
                    "actorId": teacher.id,
                },
            ),
            {},
        )
    ]


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
    assert cookie_response.json()["meta"]["requestId"] == cookie_response.headers["x-request-id"]

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


def test_content_security_policy_is_hardened_for_api_responses() -> None:
    from testpaper_backend.application import app

    csp = TestClient(app).get("/").headers["content-security-policy"]
    directives = {parts[0]: parts[1:] for directive in csp.split(";") if (parts := directive.strip().split())}

    assert directives == {
        "default-src": ["'self'"],
        "script-src": ["'self'"],
        "script-src-attr": ["'none'"],
        "style-src": ["'self'"],
        "font-src": ["'self'", "data:"],
        "img-src": ["'self'", "data:", "blob:"],
        "connect-src": ["'self'"],
        "frame-ancestors": ["'none'"],
        "base-uri": ["'self'"],
        "form-action": ["'self'"],
        "object-src": ["'none'"],
        "worker-src": ["'self'"],
    }
    for forbidden_source in ("'unsafe-inline'", "ws:", "wss:", "https://cdn.jsdelivr.net"):
        assert forbidden_source not in csp


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
    class FakeScalarResult:
        def first(self):
            return None

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def scalars(self, statement):
            return FakeScalarResult()

    monkeypatch.setattr("testpaper_backend.services.questions.SessionLocal", FakeSession)
    with pytest.raises(Exception) as exc_info:
        update_correction_status(404, 1, CorrectionStatus.accepted)
    assert getattr(exc_info.value, "status_code", None) == 404


def test_update_correction_for_other_question_returns_404(monkeypatch) -> None:
    class FakeScalarResult:
        def first(self):
            return None

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def scalars(self, statement):
            compiled = statement.compile()
            assert "question_corrections" in str(compiled)
            assert "question_id" in str(compiled)
            return FakeScalarResult()

    monkeypatch.setattr("testpaper_backend.services.questions.SessionLocal", FakeSession)
    with pytest.raises(Exception) as exc_info:
        update_correction_status(404, 1, CorrectionStatus.accepted)
    assert getattr(exc_info.value, "status_code", None) == 404


def test_teacher_cannot_manage_other_question_corrections() -> None:
    teacher = _user(9, UserRole.teacher)
    with pytest.raises(Exception) as exc_info:
        ensure_question_correction_access(SimpleNamespace(ownerId=teacher.id + 1), teacher)
    assert getattr(exc_info.value, "status_code", None) == 403


def test_update_question_correction_route_scopes_status_to_question(monkeypatch) -> None:
    teacher = _user(9, UserRole.teacher)
    question = SimpleNamespace(id=123, publicId="question-123", ownerId=teacher.id)
    calls = {}

    def fake_update(correction_id, question_id, new_status):
        calls["update"] = (correction_id, question_id, new_status)
        return SimpleNamespace(
            model_dump=lambda mode="json": {
                "id": correction_id,
                "questionId": question_id,
                "userId": 42,
                "category": "typo",
                "message": "Fix typo",
                "status": new_status.value,
                "createdAt": "2026-06-14T00:00:00Z",
                "updatedAt": "2026-06-14T00:00:00Z",
            }
        )

    monkeypatch.setattr(question_routes, "get_question_or_404", lambda public_id: question)
    monkeypatch.setattr(question_routes, "update_correction_status", fake_update)

    response = question_routes.update_question_correction(
        _request(),
        "question-123",
        77,
        QuestionCorrectionUpdate(status=CorrectionStatus.accepted),
        teacher,
        None,
    )

    assert calls["update"] == (77, question.id, CorrectionStatus.accepted)
    assert response["data"]["questionId"] == question.id


def test_update_question_correction_route_rejects_non_owner(monkeypatch) -> None:
    teacher = _user(9, UserRole.teacher)
    question = SimpleNamespace(id=123, publicId="question-123", ownerId=teacher.id + 1)

    monkeypatch.setattr(question_routes, "get_question_or_404", lambda public_id: question)
    monkeypatch.setattr(
        question_routes,
        "update_correction_status",
        lambda correction_id, question_id, new_status: pytest.fail("non-owner should not update correction status"),
    )

    with pytest.raises(HTTPException) as exc_info:
        question_routes.update_question_correction(
            _request(),
            "question-123",
            77,
            QuestionCorrectionUpdate(status=CorrectionStatus.accepted),
            teacher,
            None,
        )

    assert exc_info.value.status_code == 403


def test_delete_question_correction_route_rejects_non_owner(monkeypatch) -> None:
    teacher = _user(9, UserRole.teacher)
    question = SimpleNamespace(id=123, publicId="question-123", ownerId=teacher.id + 1)

    monkeypatch.setattr(question_routes, "get_question_or_404", lambda public_id: question)
    monkeypatch.setattr(
        question_routes,
        "delete_correction_entry",
        lambda correction_id, question_id: pytest.fail("non-owner should not delete corrections"),
    )

    with pytest.raises(HTTPException) as exc_info:
        question_routes.delete_question_correction("question-123", 77, teacher, None)

    assert exc_info.value.status_code == 403


def test_question_revisions_redact_answers_for_viewers(monkeypatch) -> None:
    question = SimpleNamespace(id=123, publicId="question-123")
    revision = QuestionRevisionEntity(
        id=1,
        questionId=question.id,
        userId=9,
        patch={"text": "Updated prompt", "answer": "secret-answer"},
        changeSummary="Updated text, answer",
        createdAt=datetime(2026, 6, 14, tzinfo=UTC),
    )
    monkeypatch.setattr(question_routes, "get_question_or_404", lambda public_id: question)
    monkeypatch.setattr(question_routes, "list_revisions", lambda question_id: [revision])

    viewer_response = question_routes.get_question_revisions(
        _request("/api/v1/questions/question-123/revisions"),
        "question-123",
        _user(11, UserRole.viewer),
    )
    teacher_response = question_routes.get_question_revisions(
        _request("/api/v1/questions/question-123/revisions"),
        "question-123",
        _user(12, UserRole.teacher),
    )

    assert viewer_response["data"][0]["patch"]["answer"] == question_routes.REDACTED_REVISION_ANSWER
    assert viewer_response["data"][0]["patch"]["text"] == "Updated prompt"
    assert teacher_response["data"][0]["patch"]["answer"] == "secret-answer"


def test_expanded_paper_route_preserves_question_fields_and_answer_gate(monkeypatch) -> None:
    viewer = _user(13, UserRole.viewer)
    paper = PaperEntity(
        id=1,
        publicId="paper-1",
        title="Expanded Paper",
        subject="Math",
        duration=60,
        totalMarks=100,
        questions=[QuestionRef(questionPublicId="question-1", orderNo=1, marks=5)],
        createdAt=datetime(2026, 6, 14, tzinfo=UTC),
        updatedAt=datetime(2026, 6, 14, tzinfo=UTC),
        ownerId=viewer.id,
    )
    include_answer_calls: list[bool] = []

    def fake_paper_with_questions(paper_arg, include_answer=True):
        include_answer_calls.append(include_answer)
        payload = paper_arg.model_dump(mode="json")
        payload["questions"] = [
            {
                "id": 10,
                "publicId": "question-1",
                "questionPublicId": "question-1",
                "orderNo": 1,
                "marks": 5,
                "type": "single_choice",
                "subjects": ["Math"],
                "difficulty": "easy",
                "tags": ["algebra"],
                "text": "2 + 2 = ?",
                "options": ["3", "4"],
                "answer": "4" if include_answer else "",
                "hasLatex": False,
                "source": "unit-test",
                "images": [],
                "scoreWeight": 1.0,
                "ownerId": viewer.id,
                "createdAt": "2026-06-14T00:00:00Z",
                "updatedAt": "2026-06-14T00:00:00Z",
            }
        ]
        return payload

    app = FastAPI()

    @app.middleware("http")
    async def request_id_middleware(request, call_next):
        request.state.request_id = "test-request"
        return await call_next(request)

    app.dependency_overrides[get_current_user] = lambda: viewer
    app.include_router(paper_routes.router)
    monkeypatch.setattr(paper_routes, "get_paper_or_404", lambda public_id: paper)
    monkeypatch.setattr(paper_routes, "paper_with_questions", fake_paper_with_questions)

    response = TestClient(app).get("/api/v1/papers/paper-1?expand=questions&includeAnswer=true")

    assert response.status_code == 200
    question = response.json()["data"]["questions"][0]
    assert include_answer_calls == [False]
    assert question["text"] == "2 + 2 = ?"
    assert question["type"] == "single_choice"
    assert question["options"] == ["3", "4"]
    assert question["answer"] == ""
    assert question["questionPublicId"] == "question-1"
    assert question["marks"] == 5


def test_draft_download_uses_submitted_question_snapshot() -> None:
    teacher = _user(14, UserRole.teacher)
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: teacher
    app.include_router(paper_routes.router)
    questions = [_draft_choice_question(index) for index in range(1, 16)]
    questions[0]["text"] = "Edited local wording"
    questions[0]["answer"] = "Edited answer"

    response = TestClient(app).post(
        "/api/v1/papers/draft-download",
        json={
            "title": "Draft Export",
            "subject": "Math",
            "duration": 60,
            "totalMarks": 10,
            "includeAnswer": True,
            "questionOrder": "paper",
            "layoutDensity": "auto",
            "questions": questions,
        },
    )

    assert response.status_code == 200
    _assert_docx_download_contract(response, "Draft Export.docx", "dense")
    assert response.headers["x-draft-export"] == "true"
    assert response.content.startswith(b"PK\x03\x04")
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")
    assert "Edited local wording" in document_xml
    assert "Edited answer" in document_xml


def test_saved_paper_download_reports_docx_headers_and_effective_layout_density(monkeypatch) -> None:
    teacher = _user(15, UserRole.teacher)
    paper = PaperEntity(
        id=1,
        publicId="paper-headers",
        title="Saved Export",
        subject="Math",
        duration=60,
        totalMarks=150,
        questions=[],
        ownerId=teacher.id,
        createdAt=datetime(2026, 6, 14, tzinfo=UTC),
        updatedAt=datetime(2026, 6, 14, tzinfo=UTC),
    )
    export_calls = []

    def fake_build_export_questions(paper_arg, question_order, include_answer):
        export_calls.append((paper_arg.publicId, question_order.value, include_answer))
        return [_draft_choice_question(index) for index in range(1, 16)]

    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: teacher
    app.include_router(paper_routes.router)
    monkeypatch.setattr(paper_routes, "get_paper_or_404", lambda public_id: paper)
    monkeypatch.setattr(paper_routes, "build_export_questions", fake_build_export_questions)

    response = TestClient(app).get(
        "/api/v1/papers/paper-headers/download",
        params={
            "format": "docx",
            "questionOrder": "paper",
            "includeAnswer": "true",
            "layoutDensity": "auto",
        },
    )

    assert response.status_code == 200
    _assert_docx_download_contract(response, "Saved Export.docx", "dense")
    assert export_calls == [("paper-headers", "paper", True)]
    assert response.content.startswith(b"PK\x03\x04")
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")
    assert "Auto density question 1." in document_xml
    assert "Answer 1" in document_xml


def test_cloud_draft_download_reports_docx_headers_and_effective_layout_density(monkeypatch) -> None:
    teacher = _user(16, UserRole.teacher)
    questions = [_draft_choice_question(index) for index in range(1, 16)]
    detail = _draft_detail(
        state={
            "includeAnswersInExport": True,
            "exportMode": "paper",
            "layoutDensity": "auto",
            "paper": {
                "title": "Cloud Draft Export",
                "subject": "Math",
                "duration": 60,
                "totalMarks": 10,
                "questions": questions,
            },
        },
    )
    calls = []

    def fake_get_shared_draft(draft_public_id, current_user):
        calls.append((draft_public_id, current_user.publicId))
        return detail

    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: teacher
    app.include_router(draft_routes.router)
    monkeypatch.setattr(draft_routes, "get_shared_draft", fake_get_shared_draft)

    response = TestClient(app).get("/api/v1/drafts/draft-cloud/download")

    assert response.status_code == 200
    _assert_docx_download_contract(response, "Cloud Draft Export.docx", "dense")
    assert response.headers["x-cloud-draft-export"] == "true"
    assert calls == [("draft-cloud", teacher.publicId)]
    assert response.content.startswith(b"PK\x03\x04")
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")
    assert "Auto density question 1." in document_xml
    assert "Answer 1" in document_xml


def test_cloud_draft_download_redacts_answers_for_viewers(monkeypatch) -> None:
    viewer = _user(17, UserRole.viewer)
    question = _draft_choice_question(1)
    question["text"] = "Viewer visible prompt."
    question["answer"] = "viewer-download-secret"
    question["originalQuestion"] = {"answer": "nested-original-secret"}
    detail = _draft_detail(
        access_role=DraftAccessRole.viewer,
        name="Viewer Draft",
        state={
            "includeAnswersInExport": True,
            "exportMode": "paper",
            "layoutDensity": "normal",
            "paper": {
                "title": "Viewer Draft",
                "subject": "Math",
                "duration": 45,
                "totalMarks": 5,
                "questions": [question],
            },
        },
    )

    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: viewer
    app.include_router(draft_routes.router)
    monkeypatch.setattr(draft_routes, "get_shared_draft", lambda draft_public_id, current_user: detail)

    response = TestClient(app).get("/api/v1/drafts/draft-cloud/download")

    assert response.status_code == 200
    _assert_docx_download_contract(response, "Viewer Draft.docx", "normal")
    assert response.headers["x-cloud-draft-export"] == "true"
    assert response.content.startswith(b"PK\x03\x04")
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")
    assert "Viewer visible prompt." in document_xml
    assert "viewer-download-secret" not in document_xml
    assert "nested-original-secret" not in document_xml


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

        def add(self, entity):
            pass

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
