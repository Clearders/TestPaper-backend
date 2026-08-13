from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("sync_assurance", ROOT / "scripts" / "check_sync_assurance.py")
assert SPEC and SPEC.loader
sync_assurance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync_assurance)
FAULT_SPEC = importlib.util.spec_from_file_location("sync_fault_model", ROOT / "tests" / "test_sync_fault_model.py")
assert FAULT_SPEC and FAULT_SPEC.loader
sync_fault_model = importlib.util.module_from_spec(FAULT_SPEC)
FAULT_SPEC.loader.exec_module(sync_fault_model)


def test_frozen_assurance_bundle_is_complete_and_payload_free() -> None:
    slo, report = sync_assurance.validate_bundle()
    assert report["operationalQualification"]["status"] == "pendingStagingCanary"
    assert all(definition["maximum"] >= 0 for definition in slo["metrics"].values())


def test_release_gate_rejects_threshold_breaches_and_missing_observations() -> None:
    slo, _ = sync_assurance.validate_bundle()
    valid = {
        "syncCycleLatencyP95Ms": 1999,
        "syncFailureRatio": 0.009,
        "syncConflictRatio": 0.5,
        "syncQueueBacklogOperations": 999,
    }
    assert sync_assurance.validate_release_observation(slo, valid) == []
    invalid = valid | {"syncFailureRatio": 0.011, "syncQueueBacklogOperations": 1001}
    failures = sync_assurance.validate_release_observation(slo, invalid)
    assert any("syncFailureRatio" in failure for failure in failures)
    assert any("syncQueueBacklogOperations" in failure for failure in failures)
    assert sync_assurance.validate_release_observation(slo, {}) == [
        "missing metric syncConflictRatio",
        "missing metric syncCycleLatencyP95Ms",
        "missing metric syncFailureRatio",
        "missing metric syncQueueBacklogOperations",
    ]


def test_qualification_metrics_are_reproduced_from_all_published_seeds() -> None:
    _, report = sync_assurance.validate_bundle()
    model = sync_fault_model._model()
    conflict_count = 0
    operation_count = 0
    maximum_backlog = 0
    for offset in range(report["qualification"]["sequencesPerRuntime"]):
        state = sync_fault_model.run_sequence(model["generator"]["baseSeed"] + offset, model)
        conflict_count += sum(result["status"] == "conflict" for result in state["server"]["responses"].values())
        operation_count += len(state["server"]["responses"])
        maximum_backlog = max(maximum_backlog, *(event["queueDepth"] for event in state["trace"]))
    assert conflict_count / operation_count == report["modelQualificationMetrics"]["randomizedConflictRatio"]
    assert maximum_backlog == report["modelQualificationMetrics"]["maximumModelQueueBacklogOperations"]
