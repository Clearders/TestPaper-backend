from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from testpaper_backend.services.attachment_maintenance import ATTACHMENT_RETENTION_DAYS, apply_attachment_reference_lifecycle


class _ReferenceSession:
    def __init__(self, reference):
        self.reference = reference

    def scalar(self, _statement):
        return self.reference


def test_delete_and_restore_propagate_without_discarding_attachment_metadata() -> None:
    now = datetime(2026, 8, 13, tzinfo=UTC)
    entity = SimpleNamespace(entity_type="attachment", owner_id=7, id=11)
    reference = SimpleNamespace(
        tombstone=False,
        deleted_at=None,
        retention_until=None,
        updated_at=now - timedelta(days=1),
        blob_id=23,
        availability="available",
        file_name="diagram.png",
        content_hash="a" * 64,
    )
    session = _ReferenceSession(reference)

    apply_attachment_reference_lifecycle(session, entity=entity, mutation_kind="delete", occurred_at=now)
    assert reference.tombstone is True
    assert reference.deleted_at == now
    assert reference.retention_until == now + timedelta(days=ATTACHMENT_RETENTION_DAYS)
    assert reference.blob_id == 23 and reference.file_name == "diagram.png"

    apply_attachment_reference_lifecycle(
        session,
        entity=entity,
        mutation_kind="restore",
        occurred_at=now + timedelta(hours=1),
    )
    assert reference.tombstone is False
    assert reference.deleted_at is None and reference.retention_until is None
    assert reference.blob_id == 23 and reference.content_hash == "a" * 64


def test_non_attachment_lifecycle_does_not_touch_reference() -> None:
    reference = SimpleNamespace(tombstone=False)
    entity = SimpleNamespace(entity_type="question", owner_id=7, id=11)
    apply_attachment_reference_lifecycle(
        _ReferenceSession(reference),
        entity=entity,
        mutation_kind="delete",
        occurred_at=datetime(2026, 8, 13, tzinfo=UTC),
    )
    assert reference.tombstone is False
