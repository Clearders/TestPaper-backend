from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit the locked production dependency graph with pip-audit.")
    parser.add_argument("--artifact-dir", type=Path, default=PROJECT_ROOT / "artifacts")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    artifact_dir = args.artifact_dir.resolve()
    artifact_dir.mkdir(parents=True, exist_ok=True)
    requirements = artifact_dir / "production-requirements.txt"
    report = artifact_dir / "dependency-audit.json"
    uv = shutil.which("uv")
    if uv is None:
        print("uv is required to export the locked dependency graph.", file=sys.stderr)
        return 2

    export = subprocess.run(
        [
            uv,
            "--quiet",
            "export",
            "--locked",
            "--no-dev",
            "--no-emit-project",
            "--format",
            "requirements-txt",
            "--output-file",
            str(requirements),
        ],
        cwd=PROJECT_ROOT,
        check=False,
    )
    if export.returncode:
        return export.returncode

    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pip_audit",
            "--requirement",
            str(requirements),
            "--format",
            "json",
            "--output",
            str(report),
            "--progress-spinner",
            "off",
        ],
        cwd=PROJECT_ROOT,
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
