from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

CHECKS = (
    ("Ruff format", [sys.executable, "-m", "ruff", "format", "--check", "."]),
    ("Ruff lint", [sys.executable, "-m", "ruff", "check", "."]),
    ("OpenAPI contract", [sys.executable, "scripts/export_openapi.py", "--check"]),
    ("Pytest", [sys.executable, "-m", "pytest", "-q"]),
    ("Migration simulation", [sys.executable, "scripts/simulate_migrations.py"]),
)


def main() -> int:
    for label, command in CHECKS:
        print(f"==> {label}", flush=True)
        completed = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
        if completed.returncode != 0:
            return completed.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
