from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = ROOT / "contracts" / "sync-fault-model-v1.json"
MASK_64 = (1 << 64) - 1


class XorShift64Star:
    def __init__(self, seed: int) -> None:
        self.state = seed or 0x9E3779B97F4A7C15

    def pick(self, upper: int) -> int:
        value = self.state
        value ^= value >> 12
        value ^= (value << 25) & MASK_64
        value ^= value >> 27
        self.state = value & MASK_64
        return ((self.state * 2685821657736338717) & MASK_64) % upper


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _model() -> dict[str, Any]:
    model = json.loads(MODEL_PATH.read_text(encoding="utf-8"))
    assert model["modelVersion"] == "sync-fault-model/v1"
    assert model["generator"]["algorithm"] == "xorshift64star"
    assert model["execution"]["pullRequestSequences"] >= 1000
    assert model["execution"]["nightlySequences"] >= 10000
    return model


def _apply(server: dict[str, Any], mutation: dict[str, Any]) -> dict[str, Any]:
    operation_id = mutation["operationId"]
    if operation_id in server["responses"]:
        return deepcopy(server["responses"][operation_id])

    entity = server["entities"][mutation["entityId"]]
    if mutation["baseVersion"] != entity["version"]:
        response = {
            "operationId": operation_id,
            "status": "conflict",
            "cloudVersion": entity["version"],
            "reason": "tombstoneDivergence" if entity["tombstone"] else "divergentContent",
        }
    elif entity["tombstone"] and mutation["kind"] != "restore":
        response = {
            "operationId": operation_id,
            "status": "conflict",
            "cloudVersion": entity["version"],
            "reason": "tombstoneDivergence",
        }
    elif not entity["tombstone"] and mutation["kind"] == "restore":
        response = {
            "operationId": operation_id,
            "status": "conflict",
            "cloudVersion": entity["version"],
            "reason": "invalidRestore",
        }
    else:
        entity["version"] += 1
        entity["tombstone"] = mutation["kind"] == "delete"
        if mutation["kind"] != "delete":
            entity["value"] = mutation["value"]
        entity["history"].append(
            {
                "version": entity["version"],
                "tombstone": entity["tombstone"],
                "operationId": operation_id,
                "kind": mutation["kind"],
            }
        )
        response = {"operationId": operation_id, "status": "applied", "acceptedVersion": entity["version"]}
    server["responses"][operation_id] = deepcopy(response)
    return response


def _settle_response(state: dict[str, Any], mutation: dict[str, Any], response: dict[str, Any]) -> None:
    device = state["devices"][mutation["device"]]
    key = mutation["entityId"]
    if device["pending"].get(key) == mutation["operationId"]:
        device["pending"].pop(key)
        cloud = state["server"]["entities"][key]
        device["knownVersions"][key] = cloud["version"]
        device["views"][key] = {"version": cloud["version"], "tombstone": cloud["tombstone"], "value": cloud["value"]}
    state["terminal"][mutation["operationId"]] = deepcopy(response)


def _deliver(state: dict[str, Any], queue_index: int, *, settle: bool) -> dict[str, Any]:
    mutation = state["queue"][queue_index]
    response = _apply(state["server"], mutation)
    if settle:
        state["queue"].pop(queue_index)
        _settle_response(state, mutation, response)
    return response


def _snapshot(state: dict[str, Any], device_index: int) -> None:
    device = state["devices"][device_index]
    for entity_id, cloud in state["server"]["entities"].items():
        if entity_id in device["pending"]:
            continue
        device["knownVersions"][entity_id] = cloud["version"]
        device["views"][entity_id] = {
            "version": cloud["version"],
            "tombstone": cloud["tombstone"],
            "value": cloud["value"],
        }


def run_sequence(seed: int, model: dict[str, Any] | None = None) -> dict[str, Any]:
    model = model or _model()
    generator = model["generator"]
    rng = XorShift64Star(seed)
    entities = {
        f"entity-{index}": {
            "version": 1,
            "tombstone": False,
            "value": f"initial-{index}",
            "history": [{"version": 1, "tombstone": False, "operationId": "initial", "kind": "create"}],
        }
        for index in range(generator["entities"])
    }
    state: dict[str, Any] = {
        "server": {"entities": entities, "responses": {}},
        "devices": [
            {
                "knownVersions": {key: 1 for key in entities},
                "views": {key: {"version": 1, "tombstone": False, "value": entity["value"]} for key, entity in entities.items()},
                "pending": {},
            }
            for _ in range(generator["devices"])
        ],
        "queue": [],
        "terminal": {},
        "trace": [],
        "faultsSeen": set(),
    }

    for step in range(generator["stepsPerSequence"]):
        device_index = rng.pick(generator["devices"])
        entity_id = f"entity-{rng.pick(generator['entities'])}"
        device = state["devices"][device_index]
        if entity_id not in device["pending"]:
            view = device["views"][entity_id]
            requested = model["operations"][rng.pick(len(model["operations"]))]
            if view["tombstone"]:
                kind = "restore" if requested != "delete" else "delete"
            else:
                kind = "update" if requested == "restore" else requested
            mutation = {
                "operationId": f"{seed}-{step}",
                "device": device_index,
                "entityId": entity_id,
                "kind": kind,
                "baseVersion": device["knownVersions"][entity_id],
                "value": f"value-{seed}-{step}",
            }
            state["queue"].append(mutation)
            device["pending"][entity_id] = mutation["operationId"]
            device["views"][entity_id] = {
                "version": view["version"],
                "tombstone": kind == "delete",
                "value": mutation["value"] if kind != "delete" else view["value"],
            }

        fault = model["faults"][rng.pick(len(model["faults"]))]
        state["faultsSeen"].add(fault)
        event: dict[str, Any] = {"step": step, "fault": fault, "queueDepth": len(state["queue"])}
        if state["queue"] and fault == "none":
            event["response"] = _deliver(state, 0, settle=True)
        elif state["queue"] and fault == "timeoutAfterCommit":
            event["response"] = _deliver(state, 0, settle=False)
        elif state["queue"] and fault == "duplicateDelivery":
            first = _deliver(state, 0, settle=False)
            replay = _deliver(state, 0, settle=False)
            assert replay == first
            mutation = state["queue"].pop(0)
            _settle_response(state, mutation, replay)
            event["response"] = replay
        elif state["queue"] and fault == "outOfOrderDelivery":
            event["response"] = _deliver(state, len(state["queue"]) - 1, settle=True)
        elif fault == "cursorExpired":
            _snapshot(state, device_index)
        # offline, clientCrash and diskWriteFailure intentionally preserve durable queue/candidate state.
        state["trace"].append(event)

    while state["queue"]:
        mutation = state["queue"][0]
        first = _deliver(state, 0, settle=False)
        replay = _deliver(state, 0, settle=False)
        assert replay == first
        state["queue"].pop(0)
        _settle_response(state, mutation, replay)
    for device_index in range(generator["devices"]):
        _snapshot(state, device_index)
    return state


def _state_diff(state: dict[str, Any]) -> dict[str, Any]:
    cloud = {
        key: {"version": value["version"], "tombstone": value["tombstone"], "value": value["value"]}
        for key, value in state["server"]["entities"].items()
    }
    return {"cloud": cloud, "devices": [device["views"] for device in state["devices"]]}


def assert_invariants(seed: int, state: dict[str, Any]) -> None:
    errors: list[str] = []
    cloud = _state_diff(state)["cloud"]
    for index, device in enumerate(state["devices"]):
        if device["views"] != cloud:
            errors.append(f"device {index} did not converge")
        if device["pending"]:
            errors.append(f"device {index} retained pending candidates")
    if state["queue"]:
        errors.append("durable queue did not settle")
    if len(state["terminal"]) != len(state["server"]["responses"]):
        errors.append("an operation disappeared without an explicit terminal response")
    for operation_id, response in state["server"]["responses"].items():
        if state["terminal"].get(operation_id) != response:
            errors.append(f"replay response changed for {operation_id}")
    for entity_id, entity in state["server"]["entities"].items():
        if entity["version"] != len(entity["history"]):
            errors.append(f"semantic version gap for {entity_id}")
        for previous, current in zip(entity["history"], entity["history"][1:], strict=False):
            if previous["tombstone"] and not current["tombstone"] and current["kind"] != "restore":
                errors.append(f"silent resurrection for {entity_id}")
    if errors:
        diagnostic = {
            "seed": seed,
            "errors": errors,
            "operationsAndFaults": state["trace"],
            "stateDiff": _state_diff(state),
        }
        raise AssertionError(f"sync fault model invariant failure: {_canonical(diagnostic)}")


def test_fixed_seed_randomized_sequences_preserve_sync_safety() -> None:
    model = _model()
    execution = model["execution"]
    count = int(os.environ.get(execution["environmentVariable"], execution["pullRequestSequences"]))
    assert count >= execution["pullRequestSequences"]
    faults_seen: set[str] = set()
    for offset in range(count):
        seed = model["generator"]["baseSeed"] + offset
        state = run_sequence(seed, model)
        assert_invariants(seed, state)
        faults_seen.update(state["faultsSeen"])
    assert faults_seen == set(model["faults"])


def test_seed_is_exactly_reproducible_and_diagnostic_is_actionable() -> None:
    model = _model()
    seed = model["generator"]["baseSeed"] + 17
    first = run_sequence(seed, model)
    second = run_sequence(seed, model)
    assert _state_diff(first) == _state_diff(second)
    first["devices"][0]["views"]["entity-0"]["version"] = 999
    try:
        assert_invariants(seed, first)
    except AssertionError as exc:
        message = str(exc)
        assert f'"seed":{seed}' in message
        assert '"operationsAndFaults"' in message
        assert '"stateDiff"' in message
    else:
        raise AssertionError("corrupted state should produce a reproduction diagnostic")
