from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from time import perf_counter
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, exists, func, select
from sqlalchemy.orm import aliased

from testpaper_backend.db import SessionLocal, SyncChangeLogRow, SyncStreamRow
from testpaper_backend.time_utils import now_utc

MINIMUM_RETENTION_DAYS = 90


@dataclass(frozen=True)
class SyncCompactionResult:
    ownerId: int
    scope: str
    applied: bool
    cutoff: datetime
    previousRetainedFromSequence: int
    newRetainedFromSequence: int
    eligibleRows: int
    deletedRows: int
    preservedRows: int
    previousSnapshotVersion: int
    newSnapshotVersion: int
    epochRotated: bool
    elapsedMilliseconds: float


def _deletable_predicate(*, owner_id: int, scope: str, boundary: int):
    later = aliased(SyncChangeLogRow)
    return (
        SyncChangeLogRow.owner_id == owner_id,
        SyncChangeLogRow.scope == scope,
        SyncChangeLogRow.sequence <= boundary,
        exists(
            select(1).where(
                later.owner_id == SyncChangeLogRow.owner_id,
                later.scope == SyncChangeLogRow.scope,
                later.entity_type == SyncChangeLogRow.entity_type,
                later.public_id == SyncChangeLogRow.public_id,
                later.sequence > SyncChangeLogRow.sequence,
            )
        ),
    )


def compact_sync_stream(
    *,
    owner_id: int,
    scope: str,
    retention_days: int = MINIMUM_RETENTION_DAYS,
    apply: bool = False,
    current_time: datetime | None = None,
    session_factory: Any | None = None,
) -> SyncCompactionResult:
    started_at = perf_counter()
    if owner_id <= 0:
        raise ValueError("owner_id must be positive")
    if not scope:
        raise ValueError("scope must not be empty")
    if retention_days < MINIMUM_RETENTION_DAYS:
        raise ValueError(f"retention_days must be at least {MINIMUM_RETENTION_DAYS}")

    current_time = current_time or now_utc()
    cutoff = current_time - timedelta(days=retention_days)
    session_factory = session_factory or SessionLocal

    with session_factory() as session:
        stream = session.scalar(
            select(SyncStreamRow).where(SyncStreamRow.owner_id == owner_id, SyncStreamRow.scope == scope).with_for_update()
        )
        if stream is None:
            return SyncCompactionResult(
                ownerId=owner_id,
                scope=scope,
                applied=False,
                cutoff=cutoff,
                previousRetainedFromSequence=0,
                newRetainedFromSequence=0,
                eligibleRows=0,
                deletedRows=0,
                preservedRows=0,
                previousSnapshotVersion=0,
                newSnapshotVersion=0,
                epochRotated=False,
                elapsedMilliseconds=(perf_counter() - started_at) * 1000,
            )

        previous_horizon = stream.retained_from_sequence
        previous_snapshot_version = stream.snapshot_version
        max_sequence = session.scalar(
            select(func.coalesce(func.max(SyncChangeLogRow.sequence), 0)).where(
                SyncChangeLogRow.owner_id == owner_id,
                SyncChangeLogRow.scope == scope,
            )
        )
        first_recent_sequence = session.scalar(
            select(func.min(SyncChangeLogRow.sequence)).where(
                SyncChangeLogRow.owner_id == owner_id,
                SyncChangeLogRow.scope == scope,
                SyncChangeLogRow.created_at > cutoff,
            )
        )
        boundary = int(max_sequence if first_recent_sequence is None else first_recent_sequence - 1)
        if boundary <= previous_horizon:
            return SyncCompactionResult(
                ownerId=owner_id,
                scope=scope,
                applied=False,
                cutoff=cutoff,
                previousRetainedFromSequence=previous_horizon,
                newRetainedFromSequence=previous_horizon,
                eligibleRows=0,
                deletedRows=0,
                preservedRows=0,
                previousSnapshotVersion=previous_snapshot_version,
                newSnapshotVersion=previous_snapshot_version,
                epochRotated=False,
                elapsedMilliseconds=(perf_counter() - started_at) * 1000,
            )

        eligible_rows = session.scalar(
            select(func.count())
            .select_from(SyncChangeLogRow)
            .where(
                SyncChangeLogRow.owner_id == owner_id,
                SyncChangeLogRow.scope == scope,
                SyncChangeLogRow.sequence <= boundary,
            )
        )
        predicates = _deletable_predicate(owner_id=owner_id, scope=scope, boundary=boundary)
        deletable_rows = session.scalar(select(func.count()).select_from(SyncChangeLogRow).where(*predicates))
        eligible_count = int(eligible_rows or 0)
        deletable_count = int(deletable_rows or 0)

        if not apply:
            return SyncCompactionResult(
                ownerId=owner_id,
                scope=scope,
                applied=False,
                cutoff=cutoff,
                previousRetainedFromSequence=previous_horizon,
                newRetainedFromSequence=boundary,
                eligibleRows=eligible_count,
                deletedRows=deletable_count,
                preservedRows=eligible_count - deletable_count,
                previousSnapshotVersion=previous_snapshot_version,
                newSnapshotVersion=previous_snapshot_version + 1,
                epochRotated=False,
                elapsedMilliseconds=(perf_counter() - started_at) * 1000,
            )

        compacted_at = current_time
        stream.retained_from_sequence = boundary
        stream.snapshot_version += 1
        stream.epoch = str(uuid4())
        stream.compacted_at = compacted_at
        stream.updated_at = compacted_at
        session.flush()
        deleted_count = session.execute(delete(SyncChangeLogRow).where(*predicates)).rowcount or 0
        if deleted_count != deletable_count:
            raise RuntimeError(
                f"sync compaction candidate count changed while the stream was locked: expected {deletable_count}, deleted {deleted_count}"
            )
        session.commit()
        return SyncCompactionResult(
            ownerId=owner_id,
            scope=scope,
            applied=True,
            cutoff=cutoff,
            previousRetainedFromSequence=previous_horizon,
            newRetainedFromSequence=boundary,
            eligibleRows=eligible_count,
            deletedRows=deleted_count,
            preservedRows=eligible_count - deleted_count,
            previousSnapshotVersion=previous_snapshot_version,
            newSnapshotVersion=stream.snapshot_version,
            epochRotated=True,
            elapsedMilliseconds=(perf_counter() - started_at) * 1000,
        )


def list_sync_streams(*, scope: str | None = None, session_factory: Any | None = None) -> list[tuple[int, str]]:
    session_factory = session_factory or SessionLocal
    with session_factory() as session:
        query = select(SyncStreamRow.owner_id, SyncStreamRow.scope)
        if scope is not None:
            query = query.where(SyncStreamRow.scope == scope)
        query = query.order_by(SyncStreamRow.owner_id, SyncStreamRow.scope)
        return [(int(owner_id), stream_scope) for owner_id, stream_scope in session.execute(query).all()]
