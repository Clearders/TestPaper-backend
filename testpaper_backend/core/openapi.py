from __future__ import annotations

from copy import deepcopy
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute
from pydantic import TypeAdapter

from testpaper_backend.schemas.common import ErrorEnvelope
from testpaper_backend.schemas.realtime import CLIENT_MESSAGE_ADAPTER, SERVER_MESSAGE_ADAPTER

API_CONTRACT_VERSION = "1.1.0"
API_CONTRACT_TITLE = "TestPaper Backend"
OPENAPI_VERSION = "3.1.0"

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

_BINARY_SCHEMA = {"type": "string", "format": "binary"}
_DOWNLOAD_HEADERS = {
    "Content-Disposition": {"description": "RFC 6266 attachment filename.", "schema": {"type": "string"}},
    "X-Export-Format": {"description": "Resolved export format.", "schema": {"type": "string", "enum": ["docx"]}},
    "X-Layout-Density": {
        "description": "Resolved document layout density.",
        "schema": {"type": "string", "enum": ["auto", "normal", "compact", "dense"]},
    },
    "X-Draft-Export": {
        "description": "Present and set to `true` for an unsaved paper-draft export.",
        "schema": {"type": "string", "enum": ["true"]},
    },
    "X-Cloud-Draft-Export": {
        "description": "Present and set to `true` for a shared cloud-draft export.",
        "schema": {"type": "string", "enum": ["true"]},
    },
}

BINARY_DOWNLOAD_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Generated document bytes.",
        "headers": _DOWNLOAD_HEADERS,
        "content": {
            DOCX_MEDIA_TYPE: {"schema": _BINARY_SCHEMA},
        },
    }
}


def stable_operation_id(route: APIRoute) -> str:
    """Use semantic Python route names instead of path-derived generator names."""
    return route.name


def _merge_adapter_schema(components: dict[str, Any], name: str, adapter: TypeAdapter[Any]) -> None:
    schema = adapter.json_schema(ref_template="#/components/schemas/{model}")
    definitions = schema.pop("$defs", {})
    component_schemas = components.setdefault("schemas", {})
    for definition_name, definition in definitions.items():
        component_schemas.setdefault(definition_name, definition)
    component_schemas[name] = schema


def _error_response(description: str) -> dict[str, Any]:
    return {
        "description": description,
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorEnvelope"}}},
    }


def _apply_http_contract(schema: dict[str, Any]) -> None:
    anonymous_prefixes = (
        "/api/v1/auth/login",
        "/api/v1/auth/register",
        "/api/v1/health/",
        "/api/v1/meta/",
        "/api/v1/public/",
    )
    unsafe_methods = {"post", "put", "patch", "delete"}

    for path, path_item in schema.get("paths", {}).items():
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete"} or not isinstance(operation, dict):
                continue

            responses = operation.setdefault("responses", {})
            responses["422"] = _error_response("Request validation failed.")
            responses.setdefault("500", _error_response("Unexpected server error."))

            if not path.startswith("/api/v1"):
                continue

            anonymous = any(path.startswith(prefix) for prefix in anonymous_prefixes)
            if anonymous:
                operation["security"] = []
            elif method in unsafe_methods:
                operation["security"] = [
                    {"cookieAuth": [], "csrfToken": []},
                    {"bearerAuth": []},
                ]
            else:
                operation["security"] = [{"cookieAuth": []}, {"bearerAuth": []}]

            if not anonymous:
                responses.setdefault("401", _error_response("Authentication is required."))
                responses.setdefault("403", _error_response("The caller is not allowed to perform this operation."))
            if "{" in path:
                responses.setdefault("404", _error_response("The requested resource was not found."))
            if method in unsafe_methods:
                responses.setdefault("429", _error_response("The write rate limit was exceeded."))
            if path.startswith("/api/v1/drafts/") and method == "patch":
                responses.setdefault("409", _error_response("The draft revision conflicts with the current server revision."))
            if path.startswith("/api/v1/banks/") and method == "post" and path.endswith("/items"):
                responses.setdefault("409", _error_response("One or more questions already exist in this bank."))


def build_openapi_contract(app: FastAPI) -> dict[str, Any]:
    schema = get_openapi(
        title=API_CONTRACT_TITLE,
        version=API_CONTRACT_VERSION,
        openapi_version=OPENAPI_VERSION,
        summary="Versioned Cloud contract shared by TestPapers Web, Desktop, and Mobile.",
        description=(
            "The canonical HTTP contract for `/api/v1`. Browser clients use Cookie + CSRF authentication; "
            "native clients inject Bearer credentials. WebSocket messages are described by `x-testpapers-websocket`."
        ),
        routes=app.routes,
    )
    components = schema.setdefault("components", {})
    components.setdefault("securitySchemes", {}).update(
        {
            "cookieAuth": {
                "type": "apiKey",
                "in": "cookie",
                "name": "testpapers_session",
                "description": "HttpOnly browser session cookie.",
            },
            "csrfToken": {
                "type": "apiKey",
                "in": "header",
                "name": "X-CSRF-Token",
                "description": "Required with cookie authentication for unsafe HTTP methods.",
            },
            "bearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "opaque",
                "description": "Native-client credential injection point. Device-session issuance is owned by CLE-18.",
            },
        }
    )
    _merge_adapter_schema(components, "ErrorEnvelope", TypeAdapter(ErrorEnvelope))
    _merge_adapter_schema(components, "RealtimeClientMessage", CLIENT_MESSAGE_ADAPTER)
    _merge_adapter_schema(components, "RealtimeServerMessage", SERVER_MESSAGE_ADAPTER)
    _apply_http_contract(schema)
    schema["x-testpapers-websocket"] = {
        "path": "/api/v1/ws",
        "security": [{"cookieAuth": []}, {"bearerAuth": []}],
        "clientMessages": {"$ref": "#/components/schemas/RealtimeClientMessage"},
        "serverMessages": {"$ref": "#/components/schemas/RealtimeServerMessage"},
        "authentication": "Credentials are accepted in Cookie or Authorization headers and never in the URL.",
        "closeCodes": {
            "1008": "Origin or authentication policy violation.",
            "1013": "Per-IP connection capacity exceeded; retry later.",
        },
    }
    return schema


def install_openapi_contract(app: FastAPI) -> None:
    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            app.openapi_schema = build_openapi_contract(app)
        return deepcopy(app.openapi_schema)

    app.openapi = custom_openapi  # type: ignore[method-assign]
