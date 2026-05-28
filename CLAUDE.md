# EdLink Rostering — Claude Code context

K-12 rostering integration framework using EdLink's OneRoster API. Ingests student, enrollment, and class data from district SIS systems through EdLink, validates it through a 5-layer pipeline, and stores it in a canonical model backed by PostgreSQL.

## Architectural principles

1. **Standards-first.** Map to OneRoster, Ed-Fi, SEDM, LTI. Not vendor-specific shapes.
2. **LEA is the tenant.** `lea_id` on every entity, every query, every cache key, every log line.
3. **Connectors are pluggable.** One `Connector` protocol; per-partner implementations.
4. **Compliance math is isolated.** IDEA timelines, FERPA disclosure logging in `compliance/`.
5. **Temporal data is append-only.** Snapshots are immutable for legal evidence.
6. **Async by default in I/O paths.**
7. **Audit everything that touches student data.**
8. **Events for cross-context decoupling, direct calls within context.**
9. **Wire format is the contract.** Spec wins over vendor docs when they disagree.
10. **Reconciliation per partner, not assumed pattern.**
11. **Fail loud on compliance.** Missed IDEA deadlines, signature failures, schema drift — make them visible.
12. **AI is augmentation, not replacement.** IEP team decides; the system supports.

## Stack

- **Backend:** Python 3.13, FastAPI, SQLAlchemy 2.x (async), Pydantic v2, pydantic-settings
- **Database:** PostgreSQL 16, Alembic migrations
- **Frontend:** React 19, Chakra UI v3, TanStack Query v5, React Router v7
- **Testing:** pytest + pytest-asyncio, Vitest + RTL + MSW, Playwright
- **Linting:** ruff, mypy (strict mode), TypeScript strict
- **Infrastructure:** Azure (mocked in dev via `infrastructure/azure_mocks/`)

## Directory structure

```
edlink_rostering/
├── api/              FastAPI routes (HTTP translation only, no business logic)
│   └── routers/      Route families (leas, syncs, quarantine, alerts, audit, ...)
├── canonical/        Domain models (Student, Enrollment, LEA)
├── cli/              Operator CLI (sync, revert, reconcile, quarantine)
├── connectors/       Connector protocol + EdLink implementation
├── core/             Settings, types, retry policy, structured logging
├── dev/              Dev-only seed data and utilities
├── events/           Event envelope and normalization
├── infrastructure/   Database engine, Azure mocks (Key Vault, App Insights)
└── services/         Business logic
    ├── validation/   5-layer validation pipeline (schema, parse, referential, thresholds)
    ├── queries/      Read-side query modules (per aggregate)
    └── ...           sync_worker, reconciliation, bulk_load, revert, alerts, etc.

web/src/              React admin dashboard
alembic/versions/     Database migrations (append-only, hand-reviewed)
architecture/         Connector framework and data model reference docs
e2e/                  Playwright end-to-end specs
fixtures/             EdLink event fixture files (JSON)
scripts/              Dev lifecycle shell scripts
tests/                Python test suite
```

## Standards

| Standard | Version | Reference |
|---|---|---|
| OneRoster | 1.2 | https://www.imsglobal.org/spec/oneroster/v1p2 |
| Ed-Fi Data Standard | 6.1 | https://docs.ed-fi.org/reference/data-exchange/data-standard/ |
| Ed-Fi SEDM | Early access | https://datastandardsunited.org/ceds-sedm |
| LTI with Advantage | 1.3 | https://www.imsglobal.org/spec/lti/v1p3 |

Cite the authoritative spec when wire-format decisions are made.

## Configuration

All env vars are centralized in `edlink_rostering/core/settings.py` (Pydantic `BaseSettings`). Copy `.env.example` to `.env` for local development. Key variables:

- `APP_DATABASE_URL` / `OPS_DATABASE_URL` — async Postgres URLs (sync worker vs CLI/API)
- `EDLINK_PROFILE=dev` — enables dev-only routes (persona switcher, test events)
- `DEV_JWT_SECRET` — HS256 signing secret for dev JWT minting
- `KEYVAULT__*` — per-LEA EdLink bearer tokens (mock Key Vault reads these)

## Testing conventions

- Python: `pytest` with `pytest-asyncio` (auto mode). Tests in `tests/`.
- Frontend: Vitest + React Testing Library + MSW. Tests colocated in `__tests__/` dirs.
- E2E: Playwright against real FastAPI + Postgres. Specs in `e2e/specs/`.
- Every mutation pathway gets three tests: client validation, server error with rollback, happy path.
- Coverage is opt-in via `scripts/cov.sh`, not baked into pytest defaults.

## Dev scripts

All scripts source `scripts/_lib.sh` which loads `.env` and computes deterministic ports from `EDLINK_PORT_BASE`.

| Script | Purpose |
|---|---|
| `scripts/db-up.sh` | Start PostgreSQL via docker-compose |
| `scripts/migrate-up.sh` | Run Alembic migrations to head |
| `scripts/seed-dev.sh` | Seed development data |
| `scripts/api-serve.sh` | Start FastAPI dev server |
| `scripts/web-dev.sh` | Start Vite dev server |
| `scripts/test.sh` | Run pytest |
| `scripts/test-web.sh` | Run Vitest |
| `scripts/e2e.sh` | Run Playwright |
| `scripts/typecheck.sh` | mypy + tsc |
| `scripts/lint.sh` | ruff |

## Code conventions

- Python package imports use `edlink_rostering.*` (the installed package name)
- `undefined` over `null` in TypeScript; use `?` optional markers
- No `import React` — automatic JSX transform
- Compose separate Create and Edit forms, not `isEdit` flags
- Scope files to a single subject, not a technical category
- Optimistic updates for all TanStack Query mutations
- RFC 7807 error responses from the API (`edlink_rostering/api/errors.py`)
- Operator auth via JWT with role-based access control (`edlink_rostering/api/auth.py`)

## Things easy to get wrong

- **IEP is student-level.** Every qualifying student has their own legal IEP document.
- **"Roster" means student/teacher/class/enrollment data flowing from SIS to apps.** Not user account lists.
- **LEAs are the tenant unit.** Authorization, data scope, and pricing are all per-LEA. "LEA" covers both traditional school districts and charter schools.
- **School year is the spring year in Ed-Fi.** `schoolYear: 2026` means 2025-2026.
- **Write-back has higher stakes than ingest.** Idempotency, audit logging, and explicit ack handling required.

## Path-targeted rules

If `.claude/rules/` exists, rules auto-load when touching matching files. Do not ignore them.
