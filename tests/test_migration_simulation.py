from __future__ import annotations

from scripts.simulate_migrations import simulate_migrations


def test_full_migration_chain_simulation() -> None:
    report = simulate_migrations()
    assert report == {
        "migrationCount": 28,
        "head": "20260823_0027",
        "seedUsers": 0,
        "seedQuestions": 10,
        "downgradeClean": True,
    }
