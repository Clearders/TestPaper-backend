# CLE-20 deployment checklist

This checklist releases the additive API contract `v1.1.0`: public bank discovery, retained publication history, and version-pinned subscriptions. The database migration is `20260809_0018` (`bank_publication_history`).

## Preconditions

- Take a verified PostgreSQL backup and record the currently deployed backend image, web image, and Alembic revision.
- Build and publish the backend image that exports `contracts/openapi.json` with `info.version` set to `1.1.0`.
- Verify the proxy sends `/api/v1/*` to FastAPI and preserves `/api/v1/ws` WebSocket upgrades; public bank routes must be reachable at the same origin as the Web app.
- Keep the previous Web build available. API v1.1 is additive, so existing v1 clients remain compatible during rollout.

## Server-first rollout

1. Put the backend into the normal rolling deployment path and deploy the v1.1 backend before the Web build.
2. Apply `alembic upgrade 20260809_0018`; verify `alembic current` reports revision `20260809_0018`.
3. Check `GET /` and `GET /api/v1/health/postgres`; confirm the generated OpenAPI document reports version `1.1.0`.
4. Run an authenticated smoke flow: create or use a public bank, publish version 1, `POST /api/v1/banks/{id}/subscribe`, then verify the response has a pinned `version` and `updatedAt`.
5. Run a public smoke flow without credentials: `GET /api/v1/public/banks` and `GET /api/v1/public/banks/{id}` return the active snapshot with answers redacted.
6. Withdraw the publication and confirm the anonymous detail route returns `404`; authenticated version history still includes the withdrawn version with `withdrawnAt`.
7. Republish, then `PATCH /api/v1/banks/{id}/subscribe` with the new active `{ "version": n }`; confirm the subscription advances only through this explicit request.
8. Deploy the Web build generated against API v1.1.0, then run the full browser regression suite and the same-origin proxy smoke test.

## Release gates

- Migration upgrade, downgrade, and round-trip checks pass in CI before production rollout.
- OpenAPI validation and non-breaking compatibility checks pass; generated frontend types and contract locks match the v1.1 contract.
- Backend integration tests cover publish, withdraw, republish, public redaction, subscription pinning, explicit update, and independent forks.
- The deployed application passes login/session, question-bank, paper export, shared-draft/WebSocket, and public-bank smoke tests.
- Monitor 4xx/5xx rates, `BANK_ALREADY_PUBLISHED` conflicts, subscription update failures, WebSocket reconnect failures, and database migration errors during the release window.

## Rollback

1. If the Web release fails, redeploy the previous Web image. It remains compatible with the additive v1.1 backend and schema.
2. If the backend application release fails, redeploy the previous backend image while leaving the additive `withdrawnAt`, `publicationId`, and `updated_at` columns in place.
3. Do not downgrade the database while any v1.1 Web client is deployed or retained subscriptions/publication history are needed. The downgrade removes pinned-subscription and withdrawal-history data.
4. A database downgrade is permitted only after all v1.1 consumers have been rolled back, a fresh backup is taken, and owners confirm loss of the new history is acceptable. Run `alembic downgrade 20260805_0017` only in that condition.
5. After rollback, verify existing v1 login, question, paper, draft, bank, export, and WebSocket flows, then investigate the failed release using retained logs and CI artifacts.
