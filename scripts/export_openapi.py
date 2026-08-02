from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from testpaper_backend.application import app  # noqa: E402

DEFAULT_OUTPUT = PROJECT_ROOT / "contracts" / "openapi.json"


def canonical_openapi_bytes() -> bytes:
    return (json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export the canonical TestPapers OpenAPI contract.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="Fail when the committed contract differs from a fresh export.")
    parser.add_argument("--expect-version", help="Fail unless OpenAPI info.version equals this release version.")
    parser.add_argument("--stdout", action="store_true", help="Write the canonical document to stdout without touching a file.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    actual_version = app.openapi()["info"]["version"]
    if args.expect_version and actual_version != args.expect_version:
        print(
            f"Contract version mismatch: expected {args.expect_version}, found {actual_version}.",
            file=sys.stderr,
        )
        return 1
    generated = canonical_openapi_bytes()
    if args.stdout:
        sys.stdout.buffer.write(generated)
        return 0
    output = args.output.resolve()
    if args.check:
        if not output.exists():
            print(f"Missing canonical contract: {output}", file=sys.stderr)
            return 1
        if output.read_bytes() != generated:
            print(f"Canonical contract is stale: {output}. Run `python scripts/export_openapi.py`.", file=sys.stderr)
            return 1
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(generated)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
