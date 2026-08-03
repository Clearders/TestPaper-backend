from __future__ import annotations

from pathlib import Path

from scripts.run_smoke_tests import PROJECT_ROOT, SMOKE_TESTS


def test_smoke_manifest_covers_required_backend_workflows() -> None:
    assert set(SMOKE_TESTS) == {
        "auth",
        "question_bank",
        "paper_generation",
        "shared_drafts",
        "comment_moderation",
        "websocket",
        "docx_export",
    }
    for node_id in SMOKE_TESTS.values():
        test_path, separator, test_name = node_id.partition("::")
        assert separator and test_name.startswith("test_")
        source = (PROJECT_ROOT / test_path).read_text(encoding="utf-8")
        assert f"def {test_name}(" in source


def test_ci_quality_gate_uses_single_check_entry_and_uploads_artifacts() -> None:
    workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "python scripts/check.py --with-postgres" in workflow
    assert "if: always()" in workflow
    assert "actions/upload-artifact" in workflow
    assert "artifacts/migration-round-trip.json" in workflow
    assert "retention-days: 14" in workflow


def test_postgres_quality_gate_persists_migration_diagnostics() -> None:
    check = (Path(__file__).resolve().parents[1] / "scripts" / "check.py").read_text(encoding="utf-8")
    assert "migration-round-trip.json" in check
    assert "--diagnostics" in check
