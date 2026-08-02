from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from testpaper_backend.config import ConfigurationError, validate_configuration


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate TestPapers backend configuration without connecting to services.")
    parser.add_argument("--env-file", type=Path, help="Load this env file without overriding variables already exported by the shell.")
    parser.add_argument("--no-database", action="store_true", help="Allow DATABASE_URL to be absent (documentation/tooling only).")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.env_file:
        if not args.env_file.is_file():
            print(f"Configuration file does not exist: {args.env_file}", file=sys.stderr)
            return 2
        load_dotenv(args.env_file, override=False)

    try:
        summary = validate_configuration(require_database=not args.no_database)
    except ConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print("Configuration valid")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0
