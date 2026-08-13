from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from testpaper_backend.schemas.sync import (
    SyncConflictReason,
    SyncConflictRecord,
    SyncConflictResolutionRequest,
    SyncConflictSnapshot,
    SyncEntityType,
    SyncMutationKind,
    SyncResolutionAction,
)
from testpaper_backend.services.conflict_rules import classify_sync_conflict


def _snapshot(kind: SyncMutationKind, digest: str, *, version: int = 2) -> SyncConflictSnapshot:
    return SyncConflictSnapshot(
        schemaVersion=1,
        version=version,
        contentHash=digest,
        mutationKind=kind,
        tombstone=kind == SyncMutationKind.delete,
        payload=None if kind == SyncMutationKind.delete else {"title": "Candidate"},
        deviceId="desktop-a",
        modifiedAt=datetime(2026, 8, 13, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    ("local", "cloud", "reason"),
    [
        (SyncMutationKind.create, SyncMutationKind.create, SyncConflictReason.concurrent_create),
        (SyncMutationKind.update, SyncMutationKind.update, SyncConflictReason.divergent_content),
        (SyncMutationKind.update, SyncMutationKind.delete, SyncConflictReason.tombstone_divergence),
        (SyncMutationKind.delete, SyncMutationKind.update, SyncConflictReason.tombstone_divergence),
        (SyncMutationKind.delete, SyncMutationKind.restore, SyncConflictReason.tombstone_divergence),
        (SyncMutationKind.restore, SyncMutationKind.update, SyncConflictReason.restore_divergence),
        (SyncMutationKind.rename, SyncMutationKind.rename, SyncConflictReason.rename_divergence),
        (SyncMutationKind.rename, SyncMutationKind.update, SyncConflictReason.rename_divergence),
    ],
)
def test_conflict_rule_catalogue(local, cloud, reason) -> None:
    assert (
        classify_sync_conflict(
            local_kind=local,
            cloud_kind=cloud,
            local_content_hash="a" * 64,
            cloud_content_hash="b" * 64,
        )
        == reason
    )


def test_identical_hash_is_a_noop_not_a_conflict() -> None:
    assert (
        classify_sync_conflict(
            local_kind=SyncMutationKind.update,
            cloud_kind=SyncMutationKind.delete,
            local_content_hash="a" * 64,
            cloud_content_hash="a" * 64,
        )
        is None
    )


def test_personal_conflict_preserves_three_way_state_and_excludes_realtime_entities() -> None:
    baseline = _snapshot(SyncMutationKind.update, "a" * 64, version=1)
    record = SyncConflictRecord(
        protocolVersion=1,
        conflictId=str(uuid4()),
        origin="personalSync",
        entityType=SyncEntityType.question,
        entityId=str(uuid4()),
        reason=SyncConflictReason.divergent_content,
        base=baseline,
        local=_snapshot(SyncMutationKind.update, "b" * 64),
        cloud=_snapshot(SyncMutationKind.update, "c" * 64),
        detectedAt=datetime(2026, 8, 13, tzinfo=UTC),
    )
    assert record.base == baseline and record.origin == "personalSync"

    with pytest.raises(ValidationError, match="limited to question, paper, and draft"):
        SyncConflictRecord(**{**record.model_dump(), "entityType": "comment"})


def test_resolution_action_requires_auditable_action_specific_links() -> None:
    common = {
        "protocolVersion": 1,
        "operationId": str(uuid4()),
        "currentVersion": 2,
        "currentContentHash": "a" * 64,
    }
    save_copy = SyncConflictResolutionRequest(
        **common,
        action=SyncResolutionAction.save_copy,
        newEntityId=str(uuid4()),
    )
    assert save_copy.newEntityId is not None

    keep_local_delete = SyncConflictResolutionRequest(**common, action=SyncResolutionAction.keep_local)
    assert keep_local_delete.payload is None

    with pytest.raises(ValidationError, match="undo alone requires"):
        SyncConflictResolutionRequest(**common, action=SyncResolutionAction.undo)
