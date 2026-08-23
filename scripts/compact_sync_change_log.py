from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from testpaper_backend.services.sync_compaction import (  # noqa: E402
    MINIMUM_RETENTION_DAYS,
    compact_sync_stream,
    list_sync_streams,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Safely compact retained Sync v1 change-log history.")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--owner-id", type=int, help="Compact one owner stream.")
    target.add_argument("--all-streams", action="store_true", help="Compact every stream matching --scope.")
    parser.add_argument("--scope", default="personal", help="Sync stream scope (default: personal).")
    parser.add_argument("--retention-days", type=int, default=MINIMUM_RETENTION_DAYS)
    parser.add_argument("--apply", action="store_true", help="Apply deletion; omission performs a dry run.")
    return parser.parse_args()


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def main() -> int:
    args = parse_args()
    if args.retention_days < MINIMUM_RETENTION_DAYS:
        print(f"--retention-days must be at least {MINIMUM_RETENTION_DAYS}", file=sys.stderr)
        return 2
    targets = list_sync_streams(scope=args.scope) if args.all_streams else [(args.owner_id, args.scope)]
    results = [
        asdict(
            compact_sync_stream(
                owner_id=owner_id,
                scope=scope,
                retention_days=args.retention_days,
                apply=args.apply,
            )
        )
        for owner_id, scope in targets
    ]
    print(json.dumps({"mode": "apply" if args.apply else "dryRun", "results": results}, default=_json_default, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
