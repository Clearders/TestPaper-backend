from __future__ import annotations

from testpaper_backend.schemas.sync import SyncConflictReason, SyncMutationKind


def classify_sync_conflict(
    *,
    local_kind: SyncMutationKind,
    cloud_kind: SyncMutationKind,
    local_content_hash: str,
    cloud_content_hash: str,
) -> SyncConflictReason | None:
    """Classify divergent personal-device changes without silently merging them."""
    if local_content_hash == cloud_content_hash:
        return None
    if local_kind == cloud_kind == SyncMutationKind.create:
        return SyncConflictReason.concurrent_create
    if SyncMutationKind.delete in {local_kind, cloud_kind}:
        return SyncConflictReason.tombstone_divergence
    if SyncMutationKind.restore in {local_kind, cloud_kind}:
        return SyncConflictReason.restore_divergence
    if SyncMutationKind.rename in {local_kind, cloud_kind}:
        return SyncConflictReason.rename_divergence
    return SyncConflictReason.divergent_content
