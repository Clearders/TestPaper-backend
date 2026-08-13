from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from testpaper_backend.api.dependencies import _rate_limit_write
from testpaper_backend.api.routes import sync as sync_routes
from testpaper_backend.schemas import AttachmentChunkReceipt, AttachmentUploadStatus
from testpaper_backend.security import get_current_sync_device, get_current_user
from testpaper_backend.services.attachment_transfers import AttachmentDownload

ATTACHMENT_ID = "11111111-1111-4111-8111-111111111111"
UPLOAD_ID = "22222222-2222-4222-8222-222222222222"
DIGEST = hashlib.sha256(b"payload").hexdigest()


def _status(*, completed: bool = False) -> AttachmentUploadStatus:
    return AttachmentUploadStatus(
        protocolVersion=1,
        uploadId=UPLOAD_ID,
        attachmentId=ATTACHMENT_ID,
        deduplicated=False,
        completed=completed,
        chunkSize=262144,
        totalChunks=1,
        uploadedBytes=7 if completed else 0,
        missingChunks=[] if completed else [0],
        expiresAt=datetime.now(UTC) + timedelta(hours=24),
        contentHash=DIGEST,
        byteSize=7,
    )


def _client() -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        request.state.request_id = "attachment-route-test"
        return await call_next(request)

    app.include_router(sync_routes.router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=7)
    app.dependency_overrides[get_current_sync_device] = lambda: "desktop-1"
    app.dependency_overrides[_rate_limit_write] = lambda: None
    return TestClient(app)


def test_upload_routes_preserve_device_scope_and_chunk_digest(monkeypatch) -> None:
    calls: dict[str, object] = {}

    def initiate(payload, *, user, device_id):
        calls["initiate"] = (payload.attachmentId, user.id, device_id)
        return _status()

    def upload_chunk(**kwargs):
        calls["chunk"] = kwargs
        return AttachmentChunkReceipt(
            protocolVersion=1,
            uploadId=UPLOAD_ID,
            ordinal=0,
            duplicate=False,
            uploadedBytes=7,
            missingChunks=[],
        )

    monkeypatch.setattr(sync_routes, "initiate_attachment_upload", initiate)
    monkeypatch.setattr(sync_routes, "upload_attachment_chunk", upload_chunk)
    client = _client()
    response = client.post(
        "/api/v1/sync/attachments/uploads",
        json={
            "protocolVersion": 1,
            "idempotencyKey": "upload-1",
            "attachmentId": ATTACHMENT_ID,
            "targetEntityId": "33333333-3333-4333-8333-333333333333",
            "contentHash": DIGEST,
            "byteSize": 7,
            "chunkSize": 262144,
            "fileName": "试卷.pdf",
            "contentType": "application/pdf",
        },
    )
    assert response.status_code == 201
    assert calls["initiate"] == (ATTACHMENT_ID, 7, "desktop-1")

    response = client.put(
        f"/api/v1/sync/attachments/uploads/{UPLOAD_ID}/chunks/0",
        content=b"payload",
        headers={"Content-Type": "application/octet-stream", "X-Chunk-SHA256": DIGEST},
    )
    assert response.status_code == 200
    assert calls["chunk"]["data"] == b"payload"
    assert calls["chunk"]["content_hash"] == DIGEST
    assert calls["chunk"]["device_id"] == "desktop-1"


def test_download_returns_verified_identity_headers(monkeypatch) -> None:
    monkeypatch.setattr(
        sync_routes,
        "download_attachment",
        lambda **_kwargs: AttachmentDownload(
            content=b"payload",
            file_name="试卷 final.pdf",
            content_type="application/pdf",
            content_hash=DIGEST,
        ),
    )
    response = _client().get(f"/api/v1/sync/attachments/{ATTACHMENT_ID}/content")
    assert response.status_code == 200
    assert response.content == b"payload"
    assert response.headers["etag"] == f'"{DIGEST}"'
    assert response.headers["x-content-sha256"] == DIGEST
    assert "filename*=UTF-8''" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "private, no-store"


def test_attachment_metadata_rejects_header_and_path_injection() -> None:
    payload = {
        "protocolVersion": 1,
        "idempotencyKey": "upload-1",
        "attachmentId": ATTACHMENT_ID,
        "targetEntityId": "33333333-3333-4333-8333-333333333333",
        "contentHash": DIGEST,
        "byteSize": 7,
        "fileName": "../secret.pdf",
        "contentType": "application/pdf\r\nX-Evil: yes",
    }
    response = _client().post("/api/v1/sync/attachments/uploads", json=payload)
    assert response.status_code == 422
