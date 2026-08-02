# Backend development environments

The backend has one repository-local startup path and one verification path. It does not assume sibling repositories or relative checkout locations.

## Shared four-repository toolchain matrix

| Repository | Current pinned toolchain | Lock / ownership boundary |
| --- | --- | --- |
| TestPapers Web | Node.js 24.x in CI | `package-lock.json`; `npm run verify` is the repository gate. |
| TestPaper Backend | CPython 3.13 in CI | `uv.lock`; `python scripts/check.py` is the repository gate. |
| TestPapers Desktop | Rust 1.94.1 in contract CI; Java 21 in CI | `Cargo.lock` pins the generated client; Python is repository-validation tooling only; the Tauri runtime is deferred to CLE-23. |
| TestPapers Mobile | Dart 3.12.2 in contract CI; Java 21 in CI | `pubspec.lock` pins the generated client; Python is repository-validation tooling only; the Flutter runtime is deferred to CLE-35. |

Each repository starts and verifies independently; no command relies on a sibling checkout or relative source dependency.

Backend containers and CI install with uv 0.11.32 and require `uv sync --locked`. Development Compose pins PostgreSQL 17, Redis 8, and the MinIO release image in `compose.yaml`; Redis/Celery and MinIO remain optional profiles.

Web, Desktop, and Mobile use their own repository startup commands. They connect to this service through a configured API endpoint; no cross-repository command assumes a shared parent directory.

## Profiles

`TESTPAPERS_ENV` accepts exactly five values:

- `local`: an individual developer process, normally with filesystem data under `.runtime/local`.
- `development`: shared cloud-feature development with local Compose dependencies.
- `test`: isolated automated test configuration and database.
- `staging`: production-shaped validation; absolute data paths and explicit origins/hosts are required.
- `production`: fail-closed security defaults; secure cookies, absolute data paths, and explicit origins/hosts are required.

`APP_ENV` is supported only as a compatibility alias. If both names are set, their values must match. The backend never chooses a profile from a filename and never implicitly reads `.env`.

## First startup

```bash
uv sync --locked
docker compose up -d postgres
testpaper-config --env-file config/env/development.env.example
alembic upgrade head
testpaper-backend
```

The sample credentials are local-only. Copy a sample to an ignored private file before changing credentials. Shell variables win over values in `--env-file`, which makes CI and secret injection deterministic.

Redis and the Celery worker are optional:

```bash
docker compose --profile async up -d
```

MinIO is optional and does not change the current filesystem upload implementation:

```bash
docker compose --profile object-storage up -d
```

All four object-store connection settings must be supplied together. The preflight reports only whether they are configured; it never prints database URLs, access keys, secret keys, cookie values, or passwords.

## Runtime data

`DATA_DIR` defaults to `.runtime`. Question images default to `${DATA_DIR}/images` and avatars to `${DATA_DIR}/avatars`; `IMAGE_UPLOAD_DIR` and `AVATAR_UPLOAD_DIR` can override them independently. Directories are created during application lifespan startup, not while importing the ASGI module. Staging and production require an absolute `DATA_DIR`.

## Verification and teardown

```bash
python scripts/check.py
docker compose config
docker compose --profile async --profile object-storage config
docker compose --profile async --profile object-storage down
```

Add `--volumes` to the final command only when intentionally deleting local PostgreSQL, Redis, MinIO, and runtime volumes.
