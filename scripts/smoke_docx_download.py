from __future__ import annotations

import argparse
import io
import os
import secrets
import sys
import zipfile
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select

from testpaper_backend.documents.paper_docx import DOCX_MEDIA_TYPE


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run an end-to-end smoke test for DOCX paper downloads.")
    parser.add_argument("--base-url", default=os.getenv("SMOKE_API_BASE", "http://127.0.0.1:8000/api/v1"))
    parser.add_argument("--username", default=os.getenv("SMOKE_USERNAME", "testpaper-smoke-admin"))
    parser.add_argument("--password", default=os.getenv("SMOKE_PASSWORD") or secrets.token_urlsafe(18))
    parser.add_argument(
        "--no-seed-user",
        action="store_true",
        help="Use an existing API user instead of seeding an admin through DATABASE_URL.",
    )
    return parser.parse_args()


def ensure_smoke_user(username: str, password: str) -> None:
    from testpaper_backend.db import SessionLocal, UserRow
    from testpaper_backend.schemas import UserRole
    from testpaper_backend.security import password_hash
    from testpaper_backend.time_utils import now_utc

    with SessionLocal() as session:
        row = session.scalars(select(UserRow).where(UserRow.username == username)).first()
        now = now_utc()
        if row is None:
            row = UserRow(
                username=username,
                display_name="DOCX Smoke Admin",
                password_hash=password_hash(password),
                role=UserRole.admin.value,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
        else:
            row.display_name = "DOCX Smoke Admin"
            row.password_hash = password_hash(password)
            row.role = UserRole.admin.value
            row.is_active = True
            row.updated_at = now
        session.commit()


def assert_ok(response: httpx.Response, expected_status: int = 200) -> dict[str, Any]:
    if response.status_code != expected_status:
        raise AssertionError(f"{response.request.method} {response.request.url} returned {response.status_code}: {response.text[:500]}")
    payload = response.json()
    if payload.get("success") is not True:
        raise AssertionError(f"{response.request.method} {response.request.url} returned an unsuccessful envelope: {payload}")
    return payload


def check_health(client: httpx.Client, path: str) -> str:
    payload = assert_ok(client.get(path))
    status = payload.get("data", {}).get("status")
    if status not in {"connected", "disconnected"}:
        raise AssertionError(f"{path} returned an unexpected health status: {payload}")
    return str(status)


def verify_docx(response: httpx.Response, title: str, question_text: str, answer: str) -> None:
    if response.status_code != 200:
        raise AssertionError(f"DOCX download returned {response.status_code}: {response.text[:500]}")
    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
    if content_type != DOCX_MEDIA_TYPE:
        raise AssertionError(f"Unexpected DOCX content-type: {response.headers.get('content-type')!r}")
    disposition = response.headers.get("content-disposition", "")
    if "attachment" not in disposition.lower() or ".docx" not in disposition.lower():
        raise AssertionError(f"Unexpected content-disposition: {disposition!r}")
    if not response.content.startswith(b"PK\x03\x04"):
        raise AssertionError("Downloaded payload is not a ZIP-based DOCX file.")

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = set(archive.namelist())
        required_parts = {"[Content_Types].xml", "_rels/.rels", "word/document.xml", "word/_rels/document.xml.rels"}
        missing = required_parts - names
        if missing:
            raise AssertionError(f"DOCX is missing required parts: {sorted(missing)}")
        document_xml = archive.read("word/document.xml").decode("utf-8")

    for expected_text in (title, question_text, answer):
        if expected_text not in document_xml:
            raise AssertionError(f"DOCX document.xml does not contain expected text: {expected_text!r}")


def main() -> int:
    args = parse_args()
    base_url = args.base_url.rstrip("/")

    if not args.no_seed_user:
        ensure_smoke_user(args.username, args.password)

    run_id = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    title = f"DOCX Smoke Paper {run_id}"
    question_text = f"Smoke question {run_id}: What is 2 + 2?"
    answer = "4"

    with httpx.Client(base_url=base_url, timeout=20.0) as client:
        postgres_status = check_health(client, "/health/postgres")
        redis_status = check_health(client, "/health/redis")

        assert_ok(client.post("/auth/login", json={"username": args.username, "password": args.password}))
        question_payload = {
            "type": "single_choice",
            "subject": "Smoke Testing",
            "difficulty": "easy",
            "tags": ["smoke", "docx"],
            "text": question_text,
            "options": ["3", "4", "5", "6"],
            "answer": answer,
            "scoreWeight": 1,
        }
        question = assert_ok(client.post("/questions", json=question_payload), expected_status=201)["data"]
        paper_payload = {
            "title": title,
            "subject": "Smoke Testing",
            "duration": 30,
            "totalMarks": 5,
            "questions": [{"questionId": question["id"], "orderNo": 1, "marks": 5}],
        }
        paper = assert_ok(client.post("/papers", json=paper_payload), expected_status=201)["data"]
        paper_id = paper["id"]

        response = client.get(f"/papers/{paper_id}/download", params={"format": "docx", "questionOrder": "paper", "includeAnswer": "true"})
        verify_docx(response, title, question_text, answer)

    print(f"OK postgres={postgres_status} redis={redis_status} paper_id={paper_id} bytes={len(response.content)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
