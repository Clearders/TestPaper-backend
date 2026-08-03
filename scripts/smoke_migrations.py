from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from testpaper_backend.config import get_database_url


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Exercise the Alembic history against a temporary PostgreSQL database.")
    parser.add_argument("--diagnostics", type=Path, help="Write the successful round-trip report as JSON.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    project_root = Path(__file__).resolve().parents[1]
    alembic_config = Config(project_root / "alembic.ini")
    head_revision = ScriptDirectory.from_config(alembic_config).get_current_head()
    if head_revision is None:
        raise RuntimeError("Alembic migration history has no head revision.")
    admin_url = make_url(get_database_url()).update_query_dict({"connect_timeout": "5"})
    database_name = f"testpaper_migration_smoke_{uuid4().hex[:12]}"
    test_url = admin_url.set(database=database_name)
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    test_engine = None
    database_created = False

    def write_diagnostics(report: dict[str, object]) -> None:
        if args.diagnostics is None:
            return
        args.diagnostics.parent.mkdir(parents=True, exist_ok=True)
        args.diagnostics.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        database_created = True

        environment = os.environ.copy()
        environment["DATABASE_URL"] = test_url.render_as_string(hide_password=False)

        def alembic(*arguments: str) -> None:
            subprocess.run([sys.executable, "-m", "alembic", *arguments], cwd=project_root, env=environment, check=True)

        alembic("upgrade", "head")

        test_engine = create_engine(test_url)
        expected_tables = {
            "alembic_version",
            "auth_tokens",
            "paper_questions",
            "papers",
            "paper_draft_collaborators",
            "paper_draft_comments",
            "paper_drafts",
            "question_corrections",
            "question_revisions",
            "questions",
            "users",
        }
        with test_engine.connect() as connection:
            tables = set(inspect(connection).get_table_names())
            assert expected_tables <= tables, expected_tables - tables
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == head_revision
            assert connection.scalar(text("SELECT COUNT(*) FROM users")) == 0
            assert connection.scalar(text("SELECT COUNT(*) FROM questions")) == 10

        test_engine.dispose()
        test_engine = None
        alembic("downgrade", "base")
        test_engine = create_engine(test_url)
        with test_engine.connect() as connection:
            remaining_tables = set(inspect(connection).get_table_names())
            assert remaining_tables <= {"alembic_version"}, remaining_tables

        test_engine.dispose()
        test_engine = None
        alembic("upgrade", "head")
        test_engine = create_engine(test_url)
        with test_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == head_revision

        report = {
            "database": database_name,
            "downgradeClean": True,
            "head": head_revision,
            "seedQuestions": 10,
            "seedUsers": 0,
            "workflow": ["upgrade head", "downgrade base", "upgrade head"],
        }
        write_diagnostics(report)
        print(f"Migration smoke test passed upgrade -> base -> upgrade at {head_revision} ({database_name})")
    except BaseException as error:
        write_diagnostics(
            {
                "database": database_name,
                "error": f"{type(error).__name__}: {error}",
                "head": head_revision,
                "workflow": ["upgrade head", "downgrade base", "upgrade head"],
            }
        )
        raise
    finally:
        if test_engine is not None:
            test_engine.dispose()
        if database_created:
            with admin_engine.connect() as connection:
                connection.execute(
                    text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :database_name"),
                    {"database_name": database_name},
                )
                connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
        admin_engine.dispose()


if __name__ == "__main__":
    main()
