from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

from testpaper_backend.db import Base

SYNC_TABLES = {
    "sync_entities",
    "sync_entity_versions",
    "sync_change_log",
    "sync_streams",
    "sync_device_cursors",
    "sync_idempotency_batches",
    "sync_operation_results",
}


def constraint_names(table_name: str, constraint_type: type) -> set[str | None]:
    table = Base.metadata.tables[table_name]
    return {constraint.name for constraint in table.constraints if isinstance(constraint, constraint_type)}


def test_sync_tables_are_registered_in_alembic_metadata() -> None:
    assert set(Base.metadata.tables) >= SYNC_TABLES


def test_projection_covers_the_seven_logical_entity_types() -> None:
    table = Base.metadata.tables["sync_entities"]
    checks = {constraint.name: str(constraint.sqltext) for constraint in table.constraints if isinstance(constraint, CheckConstraint)}

    assert set(table.columns.keys()) == {
        "id",
        "ownerId",
        "entityType",
        "publicId",
        "scope",
        "schemaVersion",
        "version",
        "contentHash",
        "payload",
        "tombstone",
        "createdAt",
        "updatedAt",
        "deletedAt",
    }
    entity_type_check = checks["ck_sync_entities_entity_type"]
    for entity_type in ("question", "paper", "draft", "attachment", "comment", "favorite", "setting"):
        assert entity_type in entity_type_check
    assert "ck_sync_entities_tombstone_deleted_at" in checks
    assert "uq_sync_entities_owner_type_public_id" in constraint_names("sync_entities", UniqueConstraint)


def test_append_only_version_and_change_invariants_are_database_backed() -> None:
    assert {
        "uq_sync_entity_versions_entity_version",
        "uq_sync_entity_versions_owner_operation",
    } <= constraint_names("sync_entity_versions", UniqueConstraint)
    assert "uq_sync_change_log_entity_version" in constraint_names("sync_change_log", UniqueConstraint)

    change_indexes = {
        index.name: tuple(column.name for column in index.columns) for index in Base.metadata.tables["sync_change_log"].indexes
    }
    assert change_indexes["ix_sync_change_log_pull"] == ("ownerId", "scope", "sequence")
    assert change_indexes["ix_sync_change_log_compaction"] == ("ownerId", "createdAt", "sequence")


def test_cursor_is_tenant_scoped_and_bound_to_a_stream_epoch() -> None:
    table = Base.metadata.tables["sync_device_cursors"]
    assert tuple(column.name for column in table.primary_key.columns) == ("ownerId", "deviceId", "scope")
    foreign_keys = [constraint for constraint in table.constraints if isinstance(constraint, ForeignKeyConstraint)]
    stream_key = next(constraint for constraint in foreign_keys if constraint.name == "fk_sync_device_cursors_stream")
    assert tuple(element.parent.name for element in stream_key.elements) == ("ownerId", "scope")
    assert tuple(element.column.table.name for element in stream_key.elements) == ("sync_streams", "sync_streams")


def test_idempotency_key_and_complete_operation_order_are_unique_per_batch() -> None:
    assert "uq_sync_batches_owner_device_key" in constraint_names("sync_idempotency_batches", UniqueConstraint)
    assert "ck_sync_batches_complete_response" in constraint_names("sync_idempotency_batches", CheckConstraint)
    assert {
        "uq_sync_operation_results_batch_ordinal",
        "uq_sync_operation_results_owner_operation",
    } <= constraint_names("sync_operation_results", UniqueConstraint)

    batch_columns = Base.metadata.tables["sync_idempotency_batches"].columns
    assert {"requestId", "lastReplayedAt"} <= set(batch_columns.keys())
