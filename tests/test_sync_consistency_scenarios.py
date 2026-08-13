from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "contracts" / "sync-consistency-v1.schema.json"
FIXTURES_PATH = ROOT / "contracts" / "sync-consistency-v1.fixtures.json"
LOCK_PATH = ROOT / "contracts" / "sync-consistency-v1.lock.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def run_scenario(scenario: dict[str, Any]) -> dict[str, Any]:
    entities: dict[tuple[str, str], dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    for operation in scenario["operations"]:
        key = (operation["entityType"], operation["entityId"])
        entity = entities.get(key)
        kind = operation["kind"]
        base_version = operation.get("baseVersion")
        if entity is None:
            assert kind in {"create", "attach"} and base_version is None
            entities[key] = {
                "entityType": key[0],
                "entityId": key[1],
                "version": 1,
                "tombstone": False,
                "payload": deepcopy(operation.get("payload")),
            }
            results.append({"operationId": operation["operationId"], "status": "applied", "acceptedVersion": 1})
            continue

        if base_version != entity["version"]:
            reason = "tombstoneDivergence" if entity["tombstone"] else "divergentContent"
            conflicts.append(
                {
                    "operationId": operation["operationId"],
                    "device": operation["device"],
                    "entityType": key[0],
                    "entityId": key[1],
                    "kind": kind,
                    "baseVersion": base_version,
                    "cloudVersion": entity["version"],
                    "reason": reason,
                }
            )
            results.append(
                {
                    "operationId": operation["operationId"],
                    "status": "conflict",
                    "cloudVersion": entity["version"],
                    "reason": reason,
                }
            )
            continue

        assert kind not in {"create"}
        entity["version"] += 1
        if kind in {"delete", "detach"}:
            entity["tombstone"] = True
        else:
            entity["tombstone"] = False
            entity["payload"] = deepcopy(operation.get("payload"))
        results.append({"operationId": operation["operationId"], "status": "applied", "acceptedVersion": entity["version"]})

    return {
        "entities": [entities[key] for key in sorted(entities)],
        "conflicts": conflicts,
        "operationResults": results,
    }


def failure_message(scenario: dict[str, Any], actual: dict[str, Any]) -> str:
    return (
        f"sync consistency mismatch seed={scenario['seed']} "
        f"operations={_canonical(scenario['operations'])} "
        f"diff={_canonical({'expected': scenario['expected'], 'actual': actual})}"
    )


def _load_and_validate_bundle() -> dict[str, Any]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    fixtures = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    schema_hash = _sha256(SCHEMA_PATH)
    fixtures_hash = _sha256(FIXTURES_PATH)
    assert lock["schemaSha256"] == schema_hash
    assert lock["fixturesSha256"] == fixtures_hash
    assert lock["semanticFingerprint"] == hashlib.sha256(f"{schema_hash}:{fixtures_hash}".encode()).hexdigest()
    assert schema["properties"]["dslVersion"]["const"] == fixtures["dslVersion"] == lock["dslVersion"]
    scenario_ids = [scenario["id"] for scenario in fixtures["scenarios"]]
    operation_ids = [operation["operationId"] for scenario in fixtures["scenarios"] for operation in scenario["operations"]]
    kinds = {operation["kind"] for scenario in fixtures["scenarios"] for operation in scenario["operations"]}
    assert len(scenario_ids) == len(set(scenario_ids))
    assert len(operation_ids) == len(set(operation_ids))
    assert kinds == {"create", "update", "delete", "restore", "attach", "detach"}
    for scenario in fixtures["scenarios"]:
        assert all(operation["device"] in scenario["devices"] for operation in scenario["operations"])
    return fixtures


def test_fixed_scenarios_converge_to_the_pinned_cross_runtime_result() -> None:
    for scenario in _load_and_validate_bundle()["scenarios"]:
        actual = run_scenario(scenario)
        assert actual == scenario["expected"], failure_message(scenario, actual)


def test_failure_output_contains_reproduction_seed_operations_and_state_diff() -> None:
    scenario = _load_and_validate_bundle()["scenarios"][0]
    actual = run_scenario(scenario)
    actual["entities"][0]["version"] = 999
    diagnostic = failure_message(scenario, actual)
    assert f"seed={scenario['seed']}" in diagnostic
    assert "operations=" in diagnostic
    assert '"expected"' in diagnostic and '"actual"' in diagnostic
