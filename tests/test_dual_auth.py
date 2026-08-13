from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from testpaper_backend.api.routes import auth as auth_routes
from testpaper_backend.db import AuthAuditLogRow, AuthTokenRow, UserRow
from testpaper_backend.schemas import NativeLoginRequest, PasswordChange, RefreshTokenRequest, TokenPair
from testpaper_backend.security import get_current_sync_device, get_user_from_token
from testpaper_backend.services import auth_sessions, profiles
from testpaper_backend.services.auth_sessions import DeviceInfo

NOW = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)


def _user_row(user_id: int = 1, *, is_active: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id,
        public_id=f"user-{user_id}",
        username=f"user{user_id}",
        display_name=f"User {user_id}",
        role="viewer",
        is_active=is_active,
        avatar_url=None,
        password_hash="hashed-old",
        created_at=NOW,
        updated_at=NOW,
    )


def _token_row(
    token: str = "token-1",
    *,
    token_type: str = "session",
    user_id: int = 1,
    expires_at: datetime | None = None,
    device_id: str | None = None,
    device_name: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    last_seen_at: datetime | None = None,
    refresh_token_id: str | None = None,
    created_at: datetime | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        token=token,
        user_id=user_id,
        token_type=token_type,
        expires_at=expires_at or NOW + timedelta(days=365),
        device_id=device_id,
        device_name=device_name,
        ip_address=ip_address,
        user_agent=user_agent,
        last_seen_at=last_seen_at,
        refresh_token_id=refresh_token_id,
        created_at=created_at or NOW,
    )


class FakeSession:
    def __init__(self, token_rows: dict[str, SimpleNamespace] | None = None, user_row: SimpleNamespace | None = None):
        self.token_rows = dict(token_rows or {})
        self.user_row = user_row
        self.added: list[object] = []
        self.deleted: list[object] = []
        self.executed: list[object] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def get(self, row_type, row_id):
        if row_type is AuthTokenRow:
            return self.token_rows.get(row_id)
        if row_type is UserRow:
            return self.user_row
        return None

    def add(self, entity):
        self.added.append(entity)

    def delete(self, entity):
        self.deleted.append(entity)

    def execute(self, statement):
        self.executed.append(statement)

    def flush(self):
        pass

    def commit(self):
        pass

    def refresh(self, row):
        pass

    def scalars(self, statement):
        return SimpleNamespace(all=lambda: list(self.token_rows.values()))


def _request(path: str = "/api/v1/auth/token") -> SimpleNamespace:
    request = SimpleNamespace()
    request.state = SimpleNamespace(request_id="test-request")
    request.headers = {}
    request.client = SimpleNamespace(host="127.0.0.1")
    request.method = "POST"
    request.url = SimpleNamespace(path=path)
    return request


# --- access credential type constraints ---


def test_refresh_token_cannot_be_used_as_access_credential(monkeypatch) -> None:
    rows = {"refresh-1": _token_row("refresh-1", token_type="refresh")}
    monkeypatch.setattr("testpaper_backend.security.SessionLocal", lambda: FakeSession(rows, _user_row()))

    with pytest.raises(HTTPException) as exc_info:
        get_user_from_token("refresh-1")

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["code"] == "INVALID_TOKEN"


def test_access_and_session_tokens_are_valid_access_credentials(monkeypatch) -> None:
    for token_type in ("access", "session"):
        rows = {f"{token_type}-1": _token_row(f"{token_type}-1", token_type=token_type)}
        monkeypatch.setattr("testpaper_backend.security.SessionLocal", lambda rows=rows: FakeSession(rows, _user_row()))

        user = get_user_from_token(f"{token_type}-1")

        assert user.id == 1
        assert user.username == "user1"


def test_sync_device_must_be_bound_to_native_access_token(monkeypatch) -> None:
    access = _token_row("access-1", token_type="access", device_id="desktop-1")
    rows = {"access-1": access}
    monkeypatch.setattr("testpaper_backend.security.SessionLocal", lambda: FakeSession(rows, _user_row()))
    request = _request("/api/v1/sync/push")
    request.headers = {"authorization": "Bearer access-1"}
    user = get_user_from_token("access-1")

    assert get_current_sync_device(request, user) == "desktop-1"


def test_browser_session_cannot_be_used_for_sync_push(monkeypatch) -> None:
    browser = _token_row("session-1", token_type="session")
    rows = {"session-1": browser}
    monkeypatch.setattr("testpaper_backend.security.SessionLocal", lambda: FakeSession(rows, _user_row()))
    request = _request("/api/v1/sync/push")
    request.headers = {"authorization": "Bearer session-1"}
    user = get_user_from_token("session-1")

    with pytest.raises(HTTPException) as exc_info:
        get_current_sync_device(request, user)

    assert exc_info.value.detail["code"] == "SYNC_DEVICE_REQUIRED"


def test_expired_token_is_deleted_and_rejected(monkeypatch) -> None:
    rows = {"expired-1": _token_row("expired-1", token_type="access", expires_at=NOW - timedelta(minutes=1))}
    session = FakeSession(rows, _user_row())
    monkeypatch.setattr("testpaper_backend.security.SessionLocal", lambda: session)

    with pytest.raises(HTTPException) as exc_info:
        get_user_from_token("expired-1")

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["code"] == "TOKEN_EXPIRED"
    assert session.deleted == [rows["expired-1"]]


# --- native refresh rotation ---


def test_refresh_rotates_and_revokes_old_pair(monkeypatch) -> None:
    old_refresh = _token_row("refresh-old", token_type="refresh", device_id="dev-1", device_name="iPhone", ip_address="1.2.3.4")
    old_access = _token_row("access-old", token_type="access", refresh_token_id="refresh-old", device_id="dev-1")
    session = FakeSession({"refresh-old": old_refresh, "access-old": old_access}, _user_row())
    monkeypatch.setattr("testpaper_backend.services.auth_sessions.SessionLocal", lambda: session)

    pair = auth_sessions.refresh_token_pair("refresh-old")

    assert isinstance(pair, TokenPair)
    assert pair.accessToken != "access-old"
    assert pair.refreshToken != "refresh-old"
    assert pair.refreshExpiresIn > pair.expiresIn
    # Old refresh and its access token are both revoked.
    assert len(session.executed) == 2
    # A refresh audit event is recorded.
    assert any(getattr(entity, "event", None) == "refresh" for entity in session.added)


def test_expired_refresh_token_is_rejected(monkeypatch) -> None:
    expired = _token_row("refresh-expired", token_type="refresh", expires_at=NOW - timedelta(minutes=1))
    session = FakeSession({"refresh-expired": expired}, _user_row())
    monkeypatch.setattr("testpaper_backend.services.auth_sessions.SessionLocal", lambda: session)

    with pytest.raises(HTTPException) as exc_info:
        auth_sessions.refresh_token_pair("refresh-expired")

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["code"] == "TOKEN_EXPIRED"


def test_non_refresh_token_cannot_refresh(monkeypatch) -> None:
    session = FakeSession({"access-1": _token_row("access-1", token_type="access")}, _user_row())
    monkeypatch.setattr("testpaper_backend.services.auth_sessions.SessionLocal", lambda: session)

    with pytest.raises(HTTPException) as exc_info:
        auth_sessions.refresh_token_pair("access-1")

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["code"] == "INVALID_TOKEN"


def test_unknown_refresh_token_is_rejected(monkeypatch) -> None:
    session = FakeSession({}, _user_row())
    monkeypatch.setattr("testpaper_backend.services.auth_sessions.SessionLocal", lambda: session)

    with pytest.raises(HTTPException) as exc_info:
        auth_sessions.refresh_token_pair("missing")

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["code"] == "INVALID_TOKEN"


# --- native login ---


def test_native_login_issues_pair_and_records_login_audit(monkeypatch) -> None:
    session = FakeSession({}, _user_row())
    monkeypatch.setattr("testpaper_backend.services.auth_sessions.SessionLocal", lambda: session)
    monkeypatch.setattr(auth_sessions, "_verify_credentials", lambda session, payload: _user_row())

    pair = auth_sessions.authenticate_native(
        SimpleNamespace(username="user1", password="Passw0rd"), DeviceInfo(device_id="dev-1", device_name="iPhone", ip_address="1.2.3.4")
    )

    assert pair.accessToken and pair.refreshToken
    assert any(getattr(entity, "event", None) == "login" for entity in session.added)
    # Two new token rows are persisted (access + refresh).
    assert len([entity for entity in session.added if isinstance(entity, AuthTokenRow)]) == 2


def test_native_login_route_returns_token_pair_without_cookies(monkeypatch) -> None:
    pair = TokenPair(
        accessToken="access-1",
        refreshToken="refresh-1",
        expiresIn=1800,
        refreshExpiresIn=2592000,
        user=SimpleNamespace(
            id=1,
            publicId="user-1",
            username="user1",
            displayName="User 1",
            role="viewer",
            permissions=["questions:read"],
            isActive=True,
            avatarUrl=None,
            createdAt=NOW,
            updatedAt=NOW,
        ),
    )
    monkeypatch.setattr(auth_routes, "authenticate_native", lambda payload, device: pair)
    set_cookie_calls = []
    monkeypatch.setattr(auth_routes, "set_auth_cookie", lambda *args: set_cookie_calls.append(args))

    body = auth_routes.native_login(
        _request(), NativeLoginRequest(username="user1", password="Passw0rd", deviceName="iPhone", deviceId="dev-1"), None
    )

    assert body["data"]["accessToken"] == "access-1"
    assert body["data"]["refreshToken"] == "refresh-1"
    # Native login must never set an auth cookie.
    assert set_cookie_calls == []


def test_native_login_route_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        NativeLoginRequest(username="user1", password="Passw0rd", deviceName="iPhone", deviceId="dev-1", extra="forbidden")


def test_refresh_route_uses_body_token(monkeypatch) -> None:
    pair = TokenPair(
        accessToken="a",
        refreshToken="r",
        expiresIn=1800,
        refreshExpiresIn=2592000,
        user=SimpleNamespace(
            id=1,
            publicId="u1",
            username="u",
            displayName="U",
            role="viewer",
            permissions=[],
            isActive=True,
            avatarUrl=None,
            createdAt=NOW,
            updatedAt=NOW,
        ),
    )
    captured: list[str] = []
    monkeypatch.setattr(auth_routes, "refresh_token_pair", lambda token: captured.append(token) or pair)

    body = auth_routes.native_refresh(_request("/api/v1/auth/token/refresh"), RefreshTokenRequest(refreshToken="refresh-1"), None)

    assert body["data"]["refreshToken"] == "r"
    assert captured == ["refresh-1"]


# --- device management ---


def test_revoke_current_device_is_rejected(monkeypatch) -> None:
    current = _token_row("access-current", token_type="access", device_id="dev-1")
    session = FakeSession({"access-current": current}, _user_row())
    monkeypatch.setattr("testpaper_backend.services.auth_sessions.SessionLocal", lambda: session)

    with pytest.raises(HTTPException) as exc_info:
        auth_sessions.revoke_device(1, "dev-1", "1.2.3.4", current_token="access-current")

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "DEVICE_IS_CURRENT"


def test_revoke_other_device_only_removes_that_device(monkeypatch) -> None:
    session = FakeSession({"access-current": _token_row("access-current", token_type="access", device_id="dev-1")}, _user_row())
    monkeypatch.setattr("testpaper_backend.services.auth_sessions.SessionLocal", lambda: session)
    where_conditions: list[tuple] = []
    monkeypatch.setattr(
        "testpaper_backend.services.auth_sessions.delete",
        lambda row_type: SimpleNamespace(where=lambda *conditions: where_conditions.append(conditions) or SimpleNamespace()),
    )

    auth_sessions.revoke_device(1, "dev-2", "1.2.3.4", current_token="access-current")

    # Delete targets user_id + device_id (two conditions on a single WHERE).
    assert len(where_conditions) == 1
    assert len(where_conditions[0]) == 2
    assert any(getattr(entity, "event", None) == "device_revoked" for entity in session.added)


def test_list_devices_marks_current_and_aggregates_device(monkeypatch) -> None:
    rows = {
        "access-1": _token_row(
            "access-1", token_type="access", device_id="dev-1", device_name="iPhone", last_seen_at=NOW + timedelta(minutes=5)
        ),
        "refresh-1": _token_row(
            "refresh-1", token_type="refresh", device_id="dev-1", device_name="iPhone", created_at=NOW - timedelta(days=1)
        ),
        "access-2": _token_row("access-2", token_type="access", device_id="dev-2", device_name="Windows PC"),
    }
    session = FakeSession(rows, _user_row())
    monkeypatch.setattr("testpaper_backend.services.auth_sessions.SessionLocal", lambda: session)

    devices = auth_sessions.list_devices(1, current_token="access-1")

    assert [device.deviceId for device in devices] == ["dev-1", "dev-2"]
    device_1 = devices[0]
    assert device_1.current is True
    assert device_1.deviceName == "iPhone"
    assert device_1.createdAt == NOW - timedelta(days=1)


# --- password change & account deletion ---


def test_password_change_keeps_current_token_and_audits(monkeypatch) -> None:
    current = _token_row("current-token", token_type="session")
    session = FakeSession({"current-token": current}, _user_row())
    monkeypatch.setattr("testpaper_backend.services.profiles.SessionLocal", lambda: session)
    monkeypatch.setattr("testpaper_backend.services.profiles.verify_password", lambda pwd, stored: (True, False))
    monkeypatch.setattr("testpaper_backend.services.profiles.password_hash", lambda pwd: "hashed")
    executed_delete = []
    monkeypatch.setattr(
        "testpaper_backend.services.profiles.delete",
        lambda row_type: SimpleNamespace(
            where=lambda *_c: executed_delete.append(_c) or SimpleNamespace(where=lambda *_c2: executed_delete.append(_c2))
        ),
    )

    profiles.change_user_password(
        1, PasswordChange(currentPassword="Oldpass1", newPassword="Newpass1"), "current-token", ip_address="1.2.3.4"
    )

    assert len(executed_delete) == 2
    assert any(getattr(entity, "event", None) == "password_changed" for entity in session.added)


def test_account_deletion_revokes_all_tokens_and_audits(monkeypatch) -> None:
    session = FakeSession({}, _user_row())
    monkeypatch.setattr("testpaper_backend.services.profiles.SessionLocal", lambda: session)
    monkeypatch.setattr("testpaper_backend.services.profiles.delete", lambda row_type: SimpleNamespace(where=lambda *_c: SimpleNamespace()))

    profiles.deactivate_user_account(1, ip_address="1.2.3.4")

    assert any(isinstance(entity, AuthAuditLogRow) and entity.event == "account_deleted" for entity in session.added)
