from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy import delete
from sqlalchemy.dialects import postgresql

from testpaper_backend.db import SyncChangeLogRow, SyncStreamRow
from testpaper_backend.services.sync_compaction import _deletable_predicate, compact_sync_stream

NOW = datetime(2026, 8, 23, tzinfo=UTC)


class FakeSession:
    def __init__(self, stream: SyncStreamRow, *, deleted_rows: int = 2) -> None:
        self.scalar_results = [stream, 12, 10, 5, deleted_rows]
        self.deleted_rows = deleted_rows
        self.flushes = 0
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def scalar(self, _statement):
        return self.scalar_results.pop(0)

    def execute(self, _statement):
        return SimpleNamespace(rowcount=self.deleted_rows)

    def flush(self) -> None:
        self.flushes += 1

    def commit(self) -> None:
        self.commits += 1


def stream() -> SyncStreamRow:
    return SyncStreamRow(
        owner_id=7,
        scope="personal",
        epoch="11111111-1111-4111-8111-111111111111",
        retained_from_sequence=3,
        snapshot_version=4,
        compacted_at=None,
        created_at=NOW,
        updated_at=NOW,
    )


def test_compaction_dry_run_reports_without_rotating_or_committing() -> None:
    current = stream()
    session = FakeSession(current)

    result = compact_sync_stream(
        owner_id=7,
        scope="personal",
        current_time=NOW,
        session_factory=lambda: session,
    )

    assert result.applied is False
    assert result.previousRetainedFromSequence == 3
    assert result.newRetainedFromSequence == 9
    assert result.eligibleRows == 5
    assert result.deletedRows == 2
    assert result.preservedRows == 3
    assert result.newSnapshotVersion == 5
    assert current.retained_from_sequence == 3
    assert session.flushes == session.commits == 0


def test_applied_compaction_advances_metadata_before_safe_delete() -> None:
    current = stream()
    original_epoch = current.epoch
    session = FakeSession(current)

    result = compact_sync_stream(
        owner_id=7,
        scope="personal",
        current_time=NOW,
        apply=True,
        session_factory=lambda: session,
    )

    assert result.applied is True
    assert result.newRetainedFromSequence == 9
    assert result.newSnapshotVersion == 5
    assert current.retained_from_sequence == 9
    assert current.snapshot_version == 5
    assert current.epoch != original_epoch
    assert current.compacted_at == NOW
    assert session.flushes == session.commits == 1


def test_deletion_requires_a_later_entity_row_at_or_below_the_boundary() -> None:
    statement = delete(SyncChangeLogRow).where(*_deletable_predicate(owner_id=7, scope="personal", boundary=9))
    sql = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))

    assert "sync_change_log_1.sequence > sync_change_log.sequence" in sql
    assert "sync_change_log_1.sequence <= 9" in sql
