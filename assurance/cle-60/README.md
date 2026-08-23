# CLE-60 independent sync review bundle

This directory is the frozen, payload-free entry point for the independent M4 sync review. The reviewer does not need access to production data or credentials.

## Reproduce the evidence

Use Python 3.13 and the locked development environment:

```bash
uv sync --locked --dev
uv run --locked python scripts/check_sync_assurance.py
uv run --locked pytest tests/test_sync_consistency_scenarios.py tests/test_sync_fault_model.py -q
SYNC_FAULT_SEQUENCES=10000 uv run --locked pytest tests/test_sync_fault_model.py -q
```

Run the real PostgreSQL migration round trip with an isolated disposable database:

```bash
MIGRATION_SMOKE_DATABASE_URL=postgresql+psycopg://postgres:postgres@127.0.0.1:5432/postgres \
  uv run --locked python scripts/check.py --with-postgres
```

The Desktop repository independently consumes the same fault-model contract in `src-tauri/tests/sync_fault_model.rs`. Its scheduled workflow runs 10,000 sequences using the pinned Rust toolchain.

## Review order

1. Read the Sync v1 ADR and schema.
2. Check additive migration and rollback evidence.
3. Verify the fixed cross-runtime fixtures and randomized fault model from their published seeds.
4. Confirm the SLO gate reports zero silent loss, duplicate semantic versions, and unconverged sequences.
5. Inspect telemetry tests to confirm only aggregate counters and timings are logged.
6. Record residual risks and either approve or file findings on CLE-60.

The qualification report is deliberately explicit that synthetic timings do not replace staging/canary SLO observations.

## Remediation evidence

The independent-review findings are addressed by commit
`3f38aabcb0edfa0545e8d25da0b6f04c2090d0f4` and GitHub pull request
`https://github.com/Clearders/TestPaper-backend/pull/27`. The frozen original report above is
unchanged; `remediation-report.json` records the follow-up PostgreSQL, Python, Rust, contract,
and dependency-audit evidence.

Change-log compaction is intentionally manual during qualification. Preview one stream first,
then use the explicit apply switch only after comparing the reported boundary and row counts:

```bash
python scripts/compact_sync_change_log.py --owner-id 123 --scope personal
python scripts/compact_sync_change_log.py --owner-id 123 --scope personal --apply
```

Automatic scheduling and broader rollout remain blocked on the real staging canary.
