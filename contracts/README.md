# TestPapers Cloud API contract

`openapi.json` is the sole machine-readable Cloud HTTP contract for Web,
Desktop, and Mobile. It is exported from the FastAPI application with stable
operation IDs, canonical JSON ordering, explicit authentication, binary
download responses, and the `x-testpapers-websocket` event contract.

## Export and validation

```bash
python scripts/export_openapi.py
python scripts/export_openapi.py --check
python scripts/export_openapi.py --check --expect-version 1.0.0
docker run --rm -v "$PWD:/spec" tufin/oasdiff:v1.27.0 validate /spec/contracts/openapi.json
```

The Backend release tag `api-v1.0.0` publishes `openapi.json` and its SHA-256.
Consumer repositories copy that immutable artifact, record its digest and
generator version in `contracts/contract.lock.json`, and commit generated code.
They never read a sibling checkout by relative path.

## Compatibility policy

- Additive changes remain on `/api/v1` and increment the contract SemVer.
- Incompatible changes add a new API major path while the prior path remains
  operational.
- A deprecated public field or operation remains available for at least 90
  days and two contract releases, whichever is longer.
- Removal requires an OpenAPI `deprecated` marker, migration notes, and
  verified upgrades for every pinned consumer.
- CI runs `oasdiff breaking --fail-on WARN`. False-positive overrides must be
  narrow, reviewed, and documented; they cannot be used to bypass a real v1
  incompatibility.

Native credential issuance, refresh-token storage, and device-session
revocation are intentionally deferred to CLE-18. This baseline only defines
the Bearer injection boundary already accepted by protected endpoints.
