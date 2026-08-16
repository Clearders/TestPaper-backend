# Attachment storage and authorization boundary

CLE-92 separates user-visible attachment metadata from content-addressed bytes. The design is
storage-provider neutral: M4 keeps using the filesystem, while a later S3 implementation can map
the same opaque `storageKey` values without changing attachment identity or authorization.

## Identities and lifecycle

- `attachment_blobs` has one row per lowercase SHA-256 digest. Its unique digest deduplicates bytes
  across references while `status=available` requires successful verification.
- `attachment_references` owns the stable public identity, filename, media type, expected digest,
  tombstone, and retention deadline. One composite key binds it to its own `attachment` Sync
  projection; another binds its owner and scope to the target Sync entity, so metadata identity
  remains independent from bytes without claiming a broader ACL than its target.
- `attachment_upload_sessions` is owner/device/idempotency scoped and stores a canonical request
  hash so key reuse with different initiation content can be rejected. Chunk receipts are keyed by
  session and ordinal, allowing CLE-95 to replay chunks and discover missing ranges exactly.
- Blob reference counts cover live references only. Database triggers update them atomically and
  retain the greatest tombstone deadline as the earliest garbage-collection boundary.

Tombstones are retained metadata, not immediate byte deletion. Garbage collection may only select
an available blob when `referenceCount=0` and `gcEligibleAt` has passed. A collector must recheck
both predicates in its delete transaction.

## Authorization

Blob IDs, hashes, and storage keys are not capabilities and must never be accepted as download
authorization. `require_attachment_download` resolves an active reference by its public ID, joins
the referenced Sync entity, applies the current user's personal-scope ACL, excludes target and
reference tombstones, and only then returns the verified blob. Missing and unauthorized references
share the same `SYNC_ENTITY_NOT_FOUND` response to prevent cross-account enumeration.

Future shared scopes must extend that target-entity ACL query explicitly. They must not add a
fallback path that reads `attachment_blobs` directly.
