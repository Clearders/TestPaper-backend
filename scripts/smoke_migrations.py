from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from testpaper_backend.config import get_database_url


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    admin_url = make_url(get_database_url()).update_query_dict({"connect_timeout": "5"})
    database_name = f"testpaper_migration_smoke_{uuid4().hex[:12]}"
    test_url = admin_url.set(database=database_name)
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    test_engine = None
    database_created = False

    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        database_created = True

        environment = os.environ.copy()
        environment["DATABASE_URL"] = test_url.render_as_string(hide_password=False)
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=project_root,
            env=environment,
            check=True,
        )

        test_engine = create_engine(test_url)
        expected_tables = {
            "alembic_version",
            "auth_tokens",
            "paper_questions",
            "papers",
            "question_corrections",
            "question_revisions",
            "questions",
            "users",
        }
        with test_engine.connect() as connection:
            tables = set(inspect(connection).get_table_names())
            assert expected_tables <= tables, expected_tables - tables
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260614_0014"
            assert connection.scalar(text("SELECT COUNT(*) FROM users")) == 0
            assert connection.scalar(text("SELECT COUNT(*) FROM questions")) == 10

        print(f"Migration smoke test passed at revision 20260614_0014 ({database_name})")
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
