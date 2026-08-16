from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID

from testpaper_backend.application import app
from testpaper_backend.core.openapi import DOCX_MEDIA_TYPE
from testpaper_backend.schemas.realtime import serialize_server_message, validate_client_message


def test_contract_has_stable_unique_operation_ids() -> None:
    operation_ids = [
        operation["operationId"]
        for path_item in app.openapi()["paths"].values()
        for method, operation in path_item.items()
        if method in {"get", "post", "put", "patch", "delete"}
    ]
    assert len(operation_ids) == len(set(operation_ids))
    assert "login" in operation_ids
    assert "download_paper" in operation_ids


def test_contract_declares_cookie_csrf_and_bearer_boundaries() -> None:
    contract = app.openapi()
    schemes = contract["components"]["securitySchemes"]
    assert schemes["cookieAuth"]["in"] == "cookie"
    assert schemes["csrfToken"]["name"] == "X-CSRF-Token"
    assert schemes["bearerAuth"]["scheme"] == "bearer"
    assert contract["paths"]["/api/v1/auth/login"]["post"]["security"] == []
    assert contract["paths"]["/api/v1/auth/token"]["post"]["security"] == []
    assert contract["paths"]["/api/v1/auth/token/refresh"]["post"]["security"] == []
    assert contract["paths"]["/api/v1/auth/refresh"]["post"]["security"] == [
        {"cookieAuth": [], "csrfToken": []},
    ]
    assert contract["paths"]["/api/v1/papers"]["post"]["security"] == [
        {"cookieAuth": [], "csrfToken": []},
        {"bearerAuth": []},
    ]
    assert contract["paths"]["/api/v1/sync/push"]["post"]["security"] == [{"bearerAuth": []}]


def test_sync_push_publishes_contract_1_2_and_stable_error_responses() -> None:
    contract = app.openapi()
    operation = contract["paths"]["/api/v1/sync/push"]["post"]

    assert contract["info"]["version"] == "1.2.0"
    assert operation["requestBody"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/SyncPushRequest"}
    assert {"409", "413", "426"} <= set(operation["responses"])
    for status_code in ("409", "413", "426"):
        assert operation["responses"][status_code]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorEnvelope"
        }


def test_sync_read_endpoints_are_bearer_only_and_publish_recovery_errors() -> None:
    contract = app.openapi()
    for path, method in (
        ("/api/v1/sync/pull", "get"),
        ("/api/v1/sync/ack", "post"),
        ("/api/v1/sync/snapshot", "get"),
    ):
        operation = contract["paths"][path][method]
        assert operation["security"] == [{"bearerAuth": []}]
        assert {"400", "410", "426"} <= set(operation["responses"])


def test_attachment_transfer_contract_is_bearer_only_and_documents_verified_bytes() -> None:
    contract = app.openapi()
    paths = contract["paths"]
    for path, method in (
        ("/api/v1/sync/attachments/uploads", "post"),
        ("/api/v1/sync/attachments/uploads/{upload_id}", "get"),
        ("/api/v1/sync/attachments/uploads/{upload_id}/chunks/{ordinal}", "put"),
        ("/api/v1/sync/attachments/uploads/{upload_id}/complete", "post"),
        ("/api/v1/sync/attachments/{attachment_id}/content", "get"),
    ):
        assert paths[path][method]["security"] == [{"bearerAuth": []}]
        assert {"409", "410", "426"} <= set(paths[path][method]["responses"])
    chunk = paths["/api/v1/sync/attachments/uploads/{upload_id}/chunks/{ordinal}"]["put"]
    assert "413" in chunk["responses"]
    download = paths["/api/v1/sync/attachments/{attachment_id}/content"]["get"]["responses"]["200"]
    assert set(download["content"]) == {"application/octet-stream"}
    assert {"Content-Disposition", "ETag", "X-Content-SHA256"} <= set(download["headers"])


def test_binary_downloads_do_not_claim_to_return_json() -> None:
    contract = app.openapi()
    for path, method in (
        ("/api/v1/papers/draft-download", "post"),
        ("/api/v1/papers/{paper_public_id}/download", "get"),
        ("/api/v1/drafts/{draft_public_id}/download", "get"),
    ):
        response = contract["paths"][path][method]["responses"]["200"]
        assert set(response["content"]) == {DOCX_MEDIA_TYPE}
        assert response["headers"]["Content-Disposition"]["schema"]["type"] == "string"
        assert "X-Draft-Export" in response["headers"]
        assert "X-Cloud-Draft-Export" in response["headers"]


def test_websocket_extension_references_machine_readable_message_unions() -> None:
    contract = app.openapi()
    websocket = contract["x-testpapers-websocket"]
    assert websocket["path"] == "/api/v1/ws"
    assert websocket["clientMessages"]["$ref"].endswith("/RealtimeClientMessage")
    assert websocket["serverMessages"]["$ref"].endswith("/RealtimeServerMessage")
    assert "RealtimeClientMessage" in contract["components"]["schemas"]
    assert "RealtimeServerMessage" in contract["components"]["schemas"]


def test_realtime_messages_are_validated_and_json_serializable() -> None:
    ping = validate_client_message({"event": "ping"})
    assert ping.event == "ping"
    pong = serialize_server_message("pong", {"serverTime": "2026-08-02T12:00:00Z"})
    assert pong["event"] == "pong"
    assert UUID(pong["eventId"])
    assert isinstance(datetime.fromisoformat(pong["occurredAt"]), datetime)
    json.dumps(pong)


def test_realtime_server_envelope_preserves_relay_metadata() -> None:
    pong = serialize_server_message(
        "pong",
        {"serverTime": "2026-08-02T12:00:00Z"},
        event_id="123e4567-e89b-12d3-a456-426614174000",
        occurred_at="2026-08-02T12:00:01Z",
    )
    assert pong["eventId"] == "123e4567-e89b-12d3-a456-426614174000"
    assert pong["occurredAt"] == "2026-08-02T12:00:01Z"


def test_all_documented_json_errors_use_error_envelope() -> None:
    contract = app.openapi()
    for path, path_item in contract["paths"].items():
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            for status_code, response in operation["responses"].items():
                if not status_code.startswith(("4", "5")):
                    continue
                schema = response["content"]["application/json"]["schema"]
                assert schema == {"$ref": "#/components/schemas/ErrorEnvelope"}, (path, method, status_code)
