from __future__ import annotations

from scripts.simulate_migrations import simulate_migrations


def test_full_migration_chain_simulation() -> None:
    report = simulate_migrations()
    assert report == {
        "migrationCount": 24,
        "head": "20260813_0024",
        "seedUsers": 0,
        "seedQuestions": 10,
        "downgradeClean": True,
    }
