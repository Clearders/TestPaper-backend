from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

SMOKE_TESTS = {
    "auth": "tests/test_regressions.py::test_auth_routes_refresh_csrf_cookie_with_auth_session_expiry",
    "question_bank": "tests/test_regressions.py::test_teacher_can_delete_owned_questions",
    "paper_generation": "tests/test_regressions.py::test_generation_rejects_subjects_that_normalize_to_empty",
    "shared_drafts": "tests/test_drafts.py::test_editor_can_request_review_but_cannot_approve",
    "comment_moderation": "tests/test_drafts.py::test_comment_update_allows_author_but_blocks_other_viewer",
    "websocket": "tests/test_realtime.py::test_broadcast_notifies_local_clients_when_redis_publish_fails",
    "docx_export": "tests/test_regressions.py::test_draft_download_uses_submitted_question_snapshot",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the stable backend workflow smoke suite.")
    parser.add_argument("--junitxml", type=Path, help="Write a JUnit XML report for CI artifacts.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    command = [sys.executable, "-m", "pytest", "-q", "--tb=short", *SMOKE_TESTS.values()]
    if args.junitxml is not None:
        args.junitxml.parent.mkdir(parents=True, exist_ok=True)
        command.extend(["--junitxml", str(args.junitxml)])
    print("Smoke coverage: " + ", ".join(SMOKE_TESTS), flush=True)
    return subprocess.run(command, cwd=PROJECT_ROOT, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
