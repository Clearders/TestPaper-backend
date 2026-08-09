from __future__ import annotations

from scripts.simulate_migrations import simulate_migrations


def test_full_migration_chain_simulation() -> None:
    report = simulate_migrations()
    assert report == {
        "migrationCount": 19,
        "head": "20260809_0019",
        "seedUsers": 0,
        "seedQuestions": 10,
        "downgradeClean": True,
    }
