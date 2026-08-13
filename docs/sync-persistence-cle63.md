# Sync v1 persistence model (CLE-63)

Status: accepted implementation baseline for Sync v1. The wire contract is pinned by
`contracts/sync-v1.lock.json`; this document defines its PostgreSQL durability boundary.

## Data model

```mermaid
erDiagram
    users ||--o{ sync_entities : owns
    users ||--o{ sync_streams : owns
    sync_entities ||--|{ sync_entity_versions : versions
    sync_entity_versions ||--|| sync_change_log : publishes
    sync_streams ||--o{ sync_device_cursors : tracks
    users ||--o{ sync_idempotency_batches : submits
    sync_idempotency_batches ||--|{ sync_operation_results : stores

    sync_entities {
        bigint id PK
        int ownerId FK
        string entityType
        uuid publicId
        string scope
        int schemaVersion
        bigint version
        sha256 contentHash
        jsonb payload
        boolean tombstone
        timestamptz createdAt
        timestamptz updatedAt
        timestamptz deletedAt
    }
    sync_entity_versions {
        bigint id PK
        bigint entityId FK
        int ownerId FK
        bigint version
        uuid operationId OWNER_UK
        bigint baseVersion
        sha256 baseHash
        jsonb payload
        boolean tombstone
    }
    sync_change_log {
        bigint sequence PK
        bigint entityVersionId FK_UK
        int ownerId
        string scope
        uuid publicId
        bigint version
    }
    sync_streams {
        int ownerId PK
        string scope PK
        uuid epoch
        bigint retainedFromSequence
        bigint snapshotVersion
    }
    sync_device_cursors {
        int ownerId PK_FK
        string deviceId PK
        string scope PK_FK
        uuid streamEpoch
        bigint cursorSequence
        timestamptz expiresAt
        timestamptz revokedAt
    }
    sync_idempotency_batches {
        bigint id PK
        int ownerId FK
        string deviceId
        string idempotencyKey UK
        sha256 requestHash
        string status
        string requestId
        jsonb responsePayload
        timestamptz lastReplayedAt
    }
    sync_operation_results {
        bigint id PK
        bigint batchId FK
        int ownerId FK
        int ordinal UK
        uuid operationId OWNER_UK
        string status
        string errorCode
    }
```

`sync_entities` is the canonical projection for the seven CLE-15 logical types: question,
paper, draft, attachment, comment, favorite, and setting. Existing question, paper, and draft
rows keep their integer primary keys and `publicId` values. Their Sync v1 identity is the tuple
`(ownerId, entityType, publicId)`; later dual-write work creates the projection with the same
`publicId`, so current CRUD routes remain compatible.

The projection owns the common envelope. `payload` is nullable for compact tombstones, while
`contentHash` always represents the canonical logical content. Delete sets `tombstone=true` and
`deletedAt`; restore creates a later accepted version with `tombstone=false`. Physical deletion
of a projection is not a user-visible delete operation.

## Transaction invariants

A successful mutation uses one PostgreSQL transaction and must perform these writes in order:

1. Lock or create the `(ownerId, entityType, publicId)` projection.
2. Verify `baseVersion` and `baseHash` against the locked projection.
3. Insert exactly one immutable `sync_entity_versions` row. Globally unique `operationId`
   prevents a replay from creating a second semantic version.
4. Update the projection to the accepted version and hash.
5. Insert exactly one `sync_change_log` row referencing the immutable version.
6. Store the ordered operation result and, when the batch settles, the complete response JSON.

The outer push transaction owns the idempotency batch. Each operation uses a savepoint. Rolling
back a savepoint removes its projection/version/change writes but retains unrelated successful
operations. A failed dependency is recorded without attempting its mutation. The batch is
`completed` only after every operation result and the replay response have been persisted.

The unique `(ownerId, deviceId, idempotencyKey)` key serializes competing first submissions.
The stored `requestHash` must match on replay; a mismatch returns `IDEMPOTENCY_MISMATCH`. A
matching completed row returns `responseStatus` and `responsePayload` without executing writes.
Operation IDs are independently unique within an account in both accepted-version and ordered
result records, so moving an operation into a different batch cannot execute it a second time.
Every replay advances `lastReplayedAt`; `requestId` is a non-secret diagnostic correlation ID.

## Cursor, compaction, and snapshot boundary

An opaque cursor encodes the account, scope, stream epoch, and last committed sequence. ACK may
only move `cursorSequence` forward after the client commits an entire pulled page. Device rows
are scoped by `(ownerId, deviceId, scope)` and bind to their owning stream with a composite
foreign key.

Compaction may remove change rows below a chosen safety boundary only after preserving entity
versions required by history and conflict recovery. It atomically advances
`retainedFromSequence`, increments `snapshotVersion`, and rotates `epoch`. A cursor is expired
when its epoch differs, its sequence precedes `retainedFromSequence`, the device has expired, or
the device was revoked. Expired clients rebuild through the consistent snapshot endpoint and
receive a cursor in the current epoch. Wall-clock ordering is never used for conflict decisions.

Completed idempotency rows outlive the maximum documented client retry interval. Cleanup must
never remove a `processing` batch and must not remove a completed batch while clients can still
legitimately replay its key.

## Query-plan baseline

The migration smoke test disables sequential scans for the empty-schema probe and verifies these
index-backed shapes against real PostgreSQL:

```sql
SELECT sequence
FROM sync_change_log
WHERE "ownerId" = $1 AND scope = $2 AND sequence > $3
ORDER BY sequence
LIMIT $4;
-- ix_sync_change_log_pull (ownerId, scope, sequence)

SELECT id
FROM sync_idempotency_batches
WHERE "ownerId" = $1 AND "deviceId" = $2 AND "idempotencyKey" = $3;
-- uq_sync_batches_owner_device_key
```

Production-like load testing in CLE-64/65 must capture `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`
for representative tenant sizes. A regression that adds a sort or a sequential scan to either
hot path blocks rollout.

## Migration and rollout

Alembic revision `20260813_0019` is additive: it creates sync-only tables and does not alter or
backfill legacy CRUD rows. Sync traffic therefore remains disabled after the schema deploy.
Follow-on work backfills projections deterministically, enables dual-write invariant checks, and
then turns on account/entity feature flags. Rollback disables sync traffic first; the additive
tables, version history, tombstones, batch responses, and pending client data are preserved.
