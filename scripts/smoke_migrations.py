from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from testpaper_backend.config import get_database_url


def exercise_sync_push(test_engine) -> None:
    from fastapi import HTTPException

    from testpaper_backend.db import SyncChangeLogRow, SyncEntityVersionRow, UserRow
    from testpaper_backend.schemas import SyncMutation, SyncOperationStatus, SyncPushRequest, UserEntity, UserRole
    from testpaper_backend.security import permissions_for_role
    from testpaper_backend.services import sync_push
    from testpaper_backend.time_utils import now_utc

    sessions = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)
    now = now_utc()
    with sessions() as session:
        user_row = UserRow(
            username="sync-smoke",
            display_name="Sync Smoke",
            password_hash="not-used",
            role=UserRole.teacher.value,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        session.add(user_row)
        session.commit()
        session.refresh(user_row)
        user = UserEntity(
            id=user_row.id,
            publicId=user_row.public_id,
            username=user_row.username,
            displayName=user_row.display_name,
            role=UserRole.teacher,
            permissions=permissions_for_role(UserRole.teacher),
            isActive=True,
            createdAt=now,
            updatedAt=now,
        )

    sync_push.SessionLocal = sessions
    entity_id = "11111111-1111-4111-8111-111111111111"
    create_payload = SyncPushRequest(
        protocolVersion=1,
        batchId="22222222-2222-4222-8222-222222222222",
        deviceId="migration-smoke",
        mutations=[
            SyncMutation(
                operationId="33333333-3333-4333-8333-333333333333",
                entityType="question",
                entityId=entity_id,
                kind="create",
                payload={"text": "original", "answer": 4},
                dependsOn=[],
            )
        ],
    )
    created = sync_push.push_mutations(
        create_payload,
        user=user,
        authenticated_device_id="migration-smoke",
        request_id="sync-smoke-create",
    )
    assert created.results[0].status == SyncOperationStatus.applied
    assert created.results[0].entityVersion == 1
    base_hash = created.results[0].contentHash
    assert base_hash is not None

    replayed = sync_push.push_mutations(
        create_payload,
        user=user,
        authenticated_device_id="migration-smoke",
        request_id="sync-smoke-replay",
    )
    assert replayed == created
    with sessions() as session:
        assert session.query(SyncEntityVersionRow).count() == 1
        assert session.query(SyncChangeLogRow).count() == 1

    mismatched = create_payload.model_copy(deep=True)
    mismatched.mutations[0].payload = {"text": "changed reuse", "answer": 4}
    try:
        sync_push.push_mutations(
            mismatched,
            user=user,
            authenticated_device_id="migration-smoke",
            request_id="sync-smoke-mismatch",
        )
    except HTTPException as error:
        assert error.detail["code"] == "SYNC_IDEMPOTENCY_MISMATCH"
    else:
        raise AssertionError("changed idempotency replay unexpectedly succeeded")

    def concurrent_update(batch_id: str, operation_id: str, text_value: str):
        return sync_push.push_mutations(
            SyncPushRequest(
                protocolVersion=1,
                batchId=batch_id,
                deviceId="migration-smoke",
                mutations=[
                    SyncMutation(
                        operationId=operation_id,
                        entityType="question",
                        entityId=entity_id,
                        kind="update",
                        baseVersion=1,
                        baseContentHash=base_hash,
                        payload={"text": text_value, "answer": 4},
                        dependsOn=[],
                    )
                ],
            ),
            user=user,
            authenticated_device_id="migration-smoke",
            request_id=f"sync-smoke-{text_value}",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                concurrent_update,
                "44444444-4444-4444-8444-444444444444",
                "55555555-5555-4555-8555-555555555555",
                "device-a",
            ),
            executor.submit(
                concurrent_update,
                "66666666-6666-4666-8666-666666666666",
                "77777777-7777-4777-8777-777777777777",
                "device-b",
            ),
        ]
        statuses = sorted(future.result().results[0].status.value for future in futures)
    assert statuses == ["applied", "conflict"]
    with sessions() as session:
        assert session.query(SyncEntityVersionRow).count() == 2
        assert session.query(SyncChangeLogRow).count() == 2


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
            "sync_change_log",
            "sync_device_cursors",
            "sync_entities",
            "sync_entity_versions",
            "sync_idempotency_batches",
            "sync_operation_results",
            "sync_streams",
            "users",
        }
        with test_engine.connect() as connection:
            tables = set(inspect(connection).get_table_names())
            assert expected_tables <= tables, expected_tables - tables
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == head_revision
            assert connection.scalar(text("SELECT COUNT(*) FROM users")) == 0
            assert connection.scalar(text("SELECT COUNT(*) FROM questions")) == 10

            connection.execute(text("SET enable_seqscan = off"))
            pull_plan = "\n".join(
                row[0]
                for row in connection.execute(
                    text(
                        "EXPLAIN (COSTS OFF) SELECT sequence FROM sync_change_log "
                        "WHERE \"ownerId\" = 1 AND scope = 'personal' AND sequence > 0 "
                        "ORDER BY sequence LIMIT 100"
                    )
                )
            )
            assert "ix_sync_change_log_pull" in pull_plan, pull_plan
            replay_plan = "\n".join(
                row[0]
                for row in connection.execute(
                    text(
                        'EXPLAIN (COSTS OFF) SELECT id FROM sync_idempotency_batches WHERE "ownerId" = 1 '
                        "AND \"deviceId\" = 'device-1' AND \"idempotencyKey\" = 'key-1'"
                    )
                )
            )
            assert "uq_sync_batches_owner_device_key" in replay_plan, replay_plan

        exercise_sync_push(test_engine)

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
