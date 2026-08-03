from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = PROJECT_ROOT / "artifacts"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the reproducible Backend quality gate.")
    parser.add_argument(
        "--with-postgres",
        action="store_true",
        help="Run the real PostgreSQL upgrade/downgrade smoke test using MIGRATION_SMOKE_DATABASE_URL.",
    )
    return parser.parse_args()


def checks(*, with_postgres: bool) -> list[tuple[str, list[str], dict[str, str] | None]]:
    items: list[tuple[str, list[str], dict[str, str] | None]] = [
        ("Ruff format", [sys.executable, "-m", "ruff", "format", "--check", "."], None),
        ("Ruff lint", [sys.executable, "-m", "ruff", "check", "."], None),
        ("OpenAPI drift", [sys.executable, "scripts/export_openapi.py", "--check"], None),
    ]
    items.extend(
        (
            f"Configuration example ({profile})",
            [sys.executable, "scripts/validate_config.py", "--env-file", f"config/env/{profile}.env.example"],
            None,
        )
        for profile in ("local", "development", "test", "staging", "production")
    )
    items.extend(
        [
            (
                "Workflow smoke tests",
                [sys.executable, "scripts/run_smoke_tests.py", "--junitxml", str(ARTIFACT_DIR / "smoke-junit.xml")],
                None,
            ),
            (
                "Full pytest suite",
                [sys.executable, "-m", "pytest", "-q", "--tb=short", "--junitxml", str(ARTIFACT_DIR / "pytest-junit.xml")],
                None,
            ),
            ("Migration simulation", [sys.executable, "scripts/simulate_migrations.py"], None),
        ]
    )
    if with_postgres:
        database_url = os.getenv("MIGRATION_SMOKE_DATABASE_URL")
        if not database_url:
            raise SystemExit("--with-postgres requires MIGRATION_SMOKE_DATABASE_URL.")
        environment = os.environ.copy()
        environment["DATABASE_URL"] = database_url
        items.append(
            (
                "PostgreSQL migration round trip",
                [
                    sys.executable,
                    "scripts/smoke_migrations.py",
                    "--diagnostics",
                    str(ARTIFACT_DIR / "migration-round-trip.json"),
                ],
                environment,
            )
        )
    items.append(
        (
            "Locked dependency vulnerability audit",
            [sys.executable, "scripts/audit_dependencies.py", "--artifact-dir", str(ARTIFACT_DIR)],
            None,
        )
    )
    return items


def main() -> int:
    args = parse_args()
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    for label, command, environment in checks(with_postgres=args.with_postgres):
        print(f"==> {label}", flush=True)
        completed = subprocess.run(command, cwd=PROJECT_ROOT, env=environment, check=False)
        if completed.returncode != 0:
            print(f"FAILED: {label} (exit {completed.returncode})", file=sys.stderr, flush=True)
            return completed.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
