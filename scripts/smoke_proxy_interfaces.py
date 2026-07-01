from __future__ import annotations

import argparse
import contextlib
import http.client
import json
import re
import socket
import sys
import threading
import time
import zipfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from testpaper_backend.documents.paper_docx import DOCX_MEDIA_TYPE


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Smoke-test frontend/backend API communication through the documented Nginx proxy layout. "
            "PostgreSQL and Redis are mocked by deterministic in-memory responses."
        )
    )
    parser.add_argument("--frontend-port", type=int, default=3000)
    parser.add_argument("--backend-port", type=int, default=8000)
    parser.add_argument("--proxy-port", type=int, default=18080)
    parser.add_argument(
        "--nginx-doc",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "TestPapers" / "docs" / "nginx-deployment.md",
    )
    return parser.parse_args()


def assert_port_available(port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        if sock.connect_ex(("127.0.0.1", port)) == 0:
            raise RuntimeError(f"127.0.0.1:{port} is already in use.")


def validate_nginx_doc(path: Path, frontend_port: int, backend_port: int) -> None:
    text = path.read_text(encoding="utf-8")
    checks = {
        f"frontend upstream 127.0.0.1:{frontend_port}": rf"upstream\s+testpapers_frontend\s*{{[^}}]*server\s+127\.0\.0\.1:{frontend_port};",
        f"backend upstream 127.0.0.1:{backend_port}": rf"upstream\s+testpaper_backend\s*{{[^}}]*server\s+127\.0\.0\.1:{backend_port};",
        "websocket location": r"location\s+/api/v1/ws\s*{[^}]*proxy_pass\s+http://testpaper_backend/api/v1/ws;",
        "api location": r"location\s+/api/v1/\s*{[^}]*proxy_pass\s+http://testpaper_backend/api/v1/;",
        "frontend fallback": r"location\s+/\s*{[^}]*proxy_pass\s+http://testpapers_frontend;",
    }
    missing = [label for label, pattern in checks.items() if not re.search(pattern, text, re.S)]
    if missing:
        raise AssertionError(f"Nginx deployment doc is missing expected rules: {', '.join(missing)}")


def json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def make_docx_bytes(title: str = "Mock Paper") -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types></Types>")
        archive.writestr("_rels/.rels", "<Relationships></Relationships>")
        archive.writestr("word/_rels/document.xml.rels", "<Relationships></Relationships>")
        archive.writestr("word/document.xml", f"<document><body>{title}</body></document>")
    return buffer.getvalue()


class MockBackendHandler(BaseHTTPRequestHandler):
    server_version = "MockTestPaperBackend/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("content-length", "0") or "0")
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _send_json(self, status_code: int, data: Any, extra_headers: dict[str, str] | None = None) -> None:
        payload = json_bytes({"success": True, "data": data, "meta": {"requestId": "mock-request"}})
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Mock-Upstream", "backend")
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:
        payload = self._read_json()
        path = urlsplit(self.path).path
        if path == "/api/v1/auth/login":
            self._send_json(
                HTTPStatus.OK,
                {
                    "expiresAt": "2026-05-11T23:59:59+08:00",
                    "user": {
                        "id": 1,
                        "username": payload.get("username", "mock-user"),
                        "displayName": "Mock User",
                        "role": "admin",
                        "permissions": ["papers:read", "papers:write", "questions:read"],
                        "isActive": True,
                        "createdAt": "2026-05-11T00:00:00+08:00",
                        "updatedAt": "2026-05-11T00:00:00+08:00",
                    },
                },
                {"Set-Cookie": "testpapers_session=mock-session; Path=/; HttpOnly; SameSite=Lax"},
            )
            return
        if path == "/api/v1/auth/register":
            self._send_json(
                HTTPStatus.CREATED,
                {
                    "expiresAt": "2026-05-11T23:59:59+08:00",
                    "user": {
                        "id": 2,
                        "username": payload.get("username", "new-user"),
                        "displayName": payload.get("displayName", "New User"),
                        "role": "viewer",
                        "permissions": ["questions:read"],
                        "isActive": True,
                        "createdAt": "2026-05-11T00:00:00+08:00",
                        "updatedAt": "2026-05-11T00:00:00+08:00",
                    },
                },
                {"Set-Cookie": "testpapers_session=mock-session; Path=/; HttpOnly; SameSite=Lax"},
            )
            return
        if path == "/api/v1/papers":
            self._send_json(HTTPStatus.CREATED, {"id": 42, "title": payload.get("title", "Mock Paper")})
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path
        if path == "/api/v1/auth/me":
            self._send_json(
                HTTPStatus.OK,
                {
                    "id": 1,
                    "username": "mock-user",
                    "displayName": "Mock User",
                    "role": "admin",
                    "permissions": ["papers:read", "papers:write", "questions:read"],
                    "isActive": True,
                    "createdAt": "2026-05-11T00:00:00+08:00",
                    "updatedAt": "2026-05-11T00:00:00+08:00",
                },
            )
            return
        if path in {"/api/v1/health/postgres", "/api/v1/health/redis"}:
            service = path.rsplit("/", 1)[-1]
            self._send_json(HTTPStatus.OK, {"status": "connected", "service": service, "mocked": True})
            return
        if path == "/api/v1/papers/42/download":
            query = parse_qs(parsed.query)
            if query.get("format", ["docx"])[0] != "docx":
                self.send_error(HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            content = make_docx_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", DOCX_MEDIA_TYPE)
            self.send_header("Content-Disposition", 'attachment; filename="mock-paper.docx"')
            self.send_header("X-Export-Format", "docx")
            self.send_header("X-Mock-Upstream", "backend")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return
        self.send_error(HTTPStatus.NOT_FOUND)


class MockFrontendHandler(BaseHTTPRequestHandler):
    server_version = "MockNuxtFrontend/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        content = b"<html><body>Mock Nuxt frontend</body></html>"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html")
        self.send_header("X-Mock-Upstream", "frontend")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


class ProxyHandler(BaseHTTPRequestHandler):
    server_version = "MockNginxProxy/1.0"
    frontend_port = 3000
    backend_port = 8000

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _upstream(self) -> tuple[int, str]:
        path = urlsplit(self.path).path
        if path == "/api/v1/ws" or path.startswith("/api/v1/"):
            return self.backend_port, "backend"
        return self.frontend_port, "frontend"

    def _forward(self) -> None:
        port, upstream = self._upstream()
        body_length = int(self.headers.get("content-length", "0") or "0")
        body = self.rfile.read(body_length) if body_length else None
        headers = {name: value for name, value in self.headers.items() if name.lower() != "host"}
        headers["Host"] = "127.0.0.1"
        headers["X-Forwarded-Proto"] = "http"

        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            connection.request(self.command, self.path, body=body, headers=headers)
            response = connection.getresponse()
            response_body = response.read()
            self.send_response(response.status, response.reason)
            skipped_headers = {"connection", "date", "server", "transfer-encoding"}
            for name, value in response.getheaders():
                if name.lower() not in skipped_headers:
                    self.send_header(name, value)
            self.send_header("X-Proxy-Upstream", upstream)
            self.end_headers()
            self.wfile.write(response_body)
        finally:
            connection.close()

    def do_GET(self) -> None:
        self._forward()

    def do_POST(self) -> None:
        self._forward()


@contextlib.contextmanager
def serve(handler: type[BaseHTTPRequestHandler], port: int):
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def request(method: str, port: int, path: str, body: dict[str, Any] | None = None, cookie: str | None = None):
    payload = json_bytes(body) if body is not None else None
    headers: dict[str, str] = {}
    if payload is not None:
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(payload))
    if cookie:
        headers["Cookie"] = cookie
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        content = response.read()
        return response.status, dict(response.getheaders()), content
    finally:
        connection.close()


def assert_json_success(status: int, headers: dict[str, str], content: bytes, expected_status: int) -> dict[str, Any]:
    if status != expected_status:
        raise AssertionError(f"Expected {expected_status}, got {status}: {content[:300]!r}")
    if headers.get("X-Proxy-Upstream") != "backend":
        raise AssertionError(f"Expected backend upstream, got headers: {headers}")
    payload = json.loads(content.decode("utf-8"))
    if payload.get("success") is not True:
        raise AssertionError(f"Expected successful envelope, got: {payload}")
    return payload


def run_smoke(args: argparse.Namespace) -> None:
    validate_nginx_doc(args.nginx_doc, args.frontend_port, args.backend_port)
    for port in {args.frontend_port, args.backend_port, args.proxy_port}:
        assert_port_available(port)

    ProxyHandler.frontend_port = args.frontend_port
    ProxyHandler.backend_port = args.backend_port

    with (
        serve(MockBackendHandler, args.backend_port),
        serve(MockFrontendHandler, args.frontend_port),
        serve(ProxyHandler, args.proxy_port),
    ):
        time.sleep(0.1)

        status, headers, content = request("POST", args.proxy_port, "/api/v1/auth/login", {"username": "admin", "password": "password"})
        assert_json_success(status, headers, content, HTTPStatus.OK)
        cookie = headers.get("Set-Cookie", "").split(";", 1)[0]
        if not cookie.startswith("testpapers_session="):
            raise AssertionError(f"Login response did not expose the session cookie through the proxy: {headers}")

        status, headers, content = request(
            "POST",
            args.proxy_port,
            "/api/v1/auth/register",
            {"username": "new-user", "password": "password", "displayName": "New User"},
        )
        assert_json_success(status, headers, content, HTTPStatus.CREATED)

        status, headers, content = request("GET", args.proxy_port, "/api/v1/auth/me", cookie=cookie)
        assert_json_success(status, headers, content, HTTPStatus.OK)

        status, headers, content = request("GET", args.proxy_port, "/api/v1/health/postgres")
        assert_json_success(status, headers, content, HTTPStatus.OK)

        status, headers, content = request("GET", args.proxy_port, "/api/v1/health/redis")
        assert_json_success(status, headers, content, HTTPStatus.OK)

        status, headers, content = request(
            "POST",
            args.proxy_port,
            "/api/v1/papers",
            {"title": "Mock Paper", "subject": "Smoke", "duration": 30, "totalMarks": 5, "questions": []},
            cookie=cookie,
        )
        assert_json_success(status, headers, content, HTTPStatus.CREATED)

        status, headers, content = request(
            "GET",
            args.proxy_port,
            "/api/v1/papers/42/download?format=docx&questionOrder=paper&includeAnswer=true",
            cookie=cookie,
        )
        if status != HTTPStatus.OK:
            raise AssertionError(f"Expected DOCX download 200, got {status}: {content[:300]!r}")
        if headers.get("X-Proxy-Upstream") != "backend":
            raise AssertionError(f"DOCX download did not route to backend: {headers}")
        if headers.get("Content-Type") != DOCX_MEDIA_TYPE:
            raise AssertionError(f"Unexpected DOCX content-type: {headers.get('Content-Type')!r}")
        if "attachment" not in headers.get("Content-Disposition", "").lower():
            raise AssertionError(f"Unexpected DOCX content-disposition: {headers.get('Content-Disposition')!r}")
        if not content.startswith(b"PK\x03\x04"):
            raise AssertionError("DOCX response is not a ZIP-based payload.")

        status, headers, content = request("GET", args.proxy_port, "/login")
        if status != HTTPStatus.OK or headers.get("X-Proxy-Upstream") != "frontend":
            raise AssertionError(f"Frontend fallback did not route to frontend: status={status}, headers={headers}")


def main() -> int:
    args = parse_args()
    try:
        run_smoke(args)
    except Exception as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1

    print(
        "OK proxy smoke passed: "
        f"/api/v1 -> 127.0.0.1:{args.backend_port}, "
        f"/ -> 127.0.0.1:{args.frontend_port}; "
        "login/register/auth-me/health/paper-download verified with mocked PostgreSQL and Redis."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
