from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from testpaper_backend.api.dependencies import _rate_limit_write
from testpaper_backend.api.routes import sync as sync_routes
from testpaper_backend.core.http import register_exception_handlers
from testpaper_backend.security import get_current_sync_device, get_current_user


def _client() -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        request.state.request_id = "sync-route-test"
        return await call_next(request)

    register_exception_handlers(app)
    app.include_router(sync_routes.router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=7)
    app.dependency_overrides[get_current_sync_device] = lambda: "desktop-1"
    app.dependency_overrides[_rate_limit_write] = lambda: None
    return TestClient(app, raise_server_exceptions=False)


def test_capabilities_publish_all_sync_v1_limits_without_device_binding() -> None:
    response = _client().get("/api/v1/sync/capabilities")

    assert response.status_code == 200
    capabilities = response.json()["data"]
    assert capabilities == {
        "protocolVersions": [1],
        "entitySchemaVersions": {
            "question": [1],
            "paper": [1],
            "draft": [1],
            "attachment": [1],
            "comment": [1],
            "favorite": [1],
            "setting": [1],
        },
        "maxMutations": 100,
        "maxMutationBytes": 1048576,
        "maxBatchBytes": 10485760,
        "idempotencyRetentionDays": 90,
        "snapshotUrl": "/api/v1/sync/snapshot",
    }


def test_101_mutations_return_stable_413_limit_details() -> None:
    mutations = [
        {
            "operationId": str(UUID(int=index + 1)),
            "entityType": "question",
            "entityId": str(UUID(int=index + 1000)),
            "kind": "create",
            "payload": {"text": str(index)},
            "dependsOn": [],
        }
        for index in range(101)
    ]
    response = _client().post(
        "/api/v1/sync/push",
        json={
            "protocolVersion": 1,
            "batchId": "44444444-4444-4444-8444-444444444444",
            "deviceId": "desktop-1",
            "mutations": mutations,
        },
    )

    assert response.status_code == 413
    assert response.json()["error"] == {
        "code": "SYNC_BATCH_TOO_LARGE",
        "message": "Sync batch contains too many mutations",
        "details": {
            "maxMutations": 100,
            "maxMutationBytes": 1048576,
            "maxBatchBytes": 10485760,
        },
    }
