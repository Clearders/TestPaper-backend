from __future__ import annotations

import logging

import pytest

from testpaper_backend.services.sync_metrics import (
    SyncObservation,
    observe_sync_call,
    reset_sync_metrics_for_test,
    sync_metric_snapshot,
)


def setup_function() -> None:
    reset_sync_metrics_for_test()


def test_sync_metrics_record_latency_failure_conflicts_and_backlog_without_identifiers(caplog) -> None:
    caplog.set_level(logging.INFO, logger="testpaper_backend.services.sync_metrics")
    secret_payload = "Bearer secret-token payload-private-question"

    observe_sync_call(
        "push",
        lambda: {"private": secret_payload},
        lambda _: SyncObservation(mutation_count=4, conflict_count=1, queue_backlog=7),
    )
    observe_sync_call("pull", lambda: "ok", lambda _: SyncObservation(queue_backlog=3))

    snapshot = sync_metric_snapshot()
    assert snapshot.operation_count == 2
    assert snapshot.failure_count == 0
    assert snapshot.failure_ratio == 0
    assert snapshot.mutation_count == 4
    assert snapshot.conflict_count == 1
    assert snapshot.conflict_ratio == 0.25
    assert snapshot.queue_backlog_latest == 3
    assert snapshot.queue_backlog_max == 7
    assert snapshot.latency_p95_ms >= 0
    assert secret_payload not in caplog.text
    assert "secret-token" not in str(snapshot.as_dict())
    assert set(snapshot.as_dict()) == {
        "operationCount",
        "failureCount",
        "failureRatio",
        "mutationCount",
        "conflictCount",
        "conflictRatio",
        "latencyP95Ms",
        "queueBacklogLatest",
        "queueBacklogMax",
    }


def test_sync_metrics_record_failures_and_reraise_without_exception_details(caplog) -> None:
    caplog.set_level(logging.INFO, logger="testpaper_backend.services.sync_metrics")

    with pytest.raises(RuntimeError, match="private-token-value"):
        observe_sync_call("ack", lambda: (_ for _ in ()).throw(RuntimeError("private-token-value")))

    snapshot = sync_metric_snapshot()
    assert snapshot.operation_count == snapshot.failure_count == 1
    assert snapshot.failure_ratio == 1
    assert "private-token-value" not in caplog.text
