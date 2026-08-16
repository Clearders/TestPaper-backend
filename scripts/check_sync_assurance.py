from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SLO_PATH = ROOT / "contracts" / "sync-slo-v1.json"
REPORT_PATH = ROOT / "assurance" / "cle-60" / "sync-assurance-report.json"
README_PATH = ROOT / "assurance" / "cle-60" / "README.md"
SECRET_PATTERN = re.compile(r"(?i)(bearer\s+[a-z0-9._-]+|-----BEGIN [A-Z ]+PRIVATE KEY-----|sk-[a-z0-9_-]{12,})")


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_bundle() -> tuple[dict[str, Any], dict[str, Any]]:
    slo = _load(SLO_PATH)
    report = _load(REPORT_PATH)
    assert slo["contractVersion"] == "sync-slo/v1"
    assert report["reportVersion"] == "cle-60-sync-assurance/v1"
    assert report["sloContract"] == str(SLO_PATH.relative_to(ROOT)).replace("\\", "/")
    assert report["frozenForIssue"] == "CLE-60"
    assert report["qualification"]["sequencesPerRuntime"] >= slo["window"]["qualificationSequencesPerRuntime"]
    for metric in ("silentDataLossCount", "duplicateSemanticVersionCount", "unconvergedSequenceCount"):
        assert report["qualification"][metric] <= slo["metrics"][metric]["maximum"]
    assert report["modelQualificationMetrics"]["randomizedConflictRatio"] <= slo["metrics"]["syncConflictRatio"]["maximum"]
    assert (
        report["modelQualificationMetrics"]["maximumModelQueueBacklogOperations"] <= slo["metrics"]["syncQueueBacklogOperations"]["maximum"]
    )
    for relative in report["evidence"]:
        assert (ROOT / relative).is_file(), f"missing evidence: {relative}"
    assert README_PATH.is_file()
    assert report["dataHandling"] == {
        "containsBusinessPayload": False,
        "containsCredentials": False,
        "containsStableEntityOrDeviceIdentifiers": False,
    }
    evidence_text = REPORT_PATH.read_text(encoding="utf-8") + README_PATH.read_text(encoding="utf-8")
    assert not SECRET_PATTERN.search(evidence_text), "evidence bundle contains a secret-like value"
    prohibited = set(slo["telemetryProhibited"])
    allowlist = set(slo["telemetryAllowlist"])
    assert prohibited.isdisjoint(allowlist)
    return slo, report


def validate_release_observation(slo: dict[str, Any], observation: dict[str, float]) -> list[str]:
    failures: list[str] = []
    for metric, value in observation.items():
        definition = slo["metrics"].get(metric)
        if definition is None:
            failures.append(f"unknown metric {metric}")
        elif value > definition["maximum"]:
            failures.append(f"{metric}={value} exceeds maximum {definition['maximum']}")
    expected = {
        "syncCycleLatencyP95Ms",
        "syncFailureRatio",
        "syncConflictRatio",
        "syncQueueBacklogOperations",
    }
    for missing in sorted(expected - observation.keys()):
        failures.append(f"missing metric {missing}")
    return failures


def release_observation_from_environment() -> dict[str, float]:
    mapping = {
        "syncCycleLatencyP95Ms": "SYNC_SLO_LATENCY_P95_MS",
        "syncFailureRatio": "SYNC_SLO_FAILURE_RATIO",
        "syncConflictRatio": "SYNC_SLO_CONFLICT_RATIO",
        "syncQueueBacklogOperations": "SYNC_SLO_QUEUE_BACKLOG",
    }
    observation: dict[str, float] = {}
    for metric, variable in mapping.items():
        value = os.getenv(variable)
        if value is not None:
            observation[metric] = float(value)
    return observation


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the frozen CLE-60 sync assurance evidence and release SLOs.")
    parser.add_argument("--release-from-environment", action="store_true")
    args = parser.parse_args()
    slo, report = validate_bundle()
    if args.release_from_environment:
        failures = validate_release_observation(slo, release_observation_from_environment())
        if failures:
            print("Sync release gate failed:", file=sys.stderr)
            for failure in failures:
                print(f"- {failure}", file=sys.stderr)
            return 1
        print("Sync release gate passed for the staging/canary observation window.")
    else:
        qualification = report["qualification"]
        print(
            "Sync assurance bundle verified "
            f"({qualification['sequencesPerRuntime']} sequences/runtime; zero silent loss, duplicate versions, and divergence)."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
