# EdLink Rostering

Production-grade K-12 rostering integration framework using EdLink's OneRoster API.

## What it does

- **Connector protocol** — pluggable `Connector` interface that abstracts partner-specific auth, pagination, and event normalization. Ships with an EdLink implementation and a null connector for testing.
- **5-layer validation pipeline** — Layer 1 (transport), Layer 2 (schema), Layer 3 (parse/normalize), Layer 4 (referential integrity), Layer 5 (threshold analysis). Each layer has distinct failure semantics: abort, quarantine, or warn.
- **Reconciliation engine** — Merkle-tree comparison between connector state and local canonical data, with configurable drift detection and automated sweep scheduling.
- **Admin dashboard** — React + Chakra UI v3 operator interface with per-LEA detail drawers, KPI strips, alert banners, quarantine management, audit timeline, and connector lifecycle controls.

## Architecture

```
EdLink Events API
    │
    ▼
┌──────────┐    ┌────────────────┐    ┌───────────┐    ┌──────────┐
│ Connector├───►│ Validation     ├───►│ Canonical ├───►│ Postgres │
│ (fetch)  │    │ (5 layers)     │    │ (upsert)  │    │ (storage)│
└──────────┘    └───────┬────────┘    └───────────┘    └──────────┘
                        │
                        ▼
                  ┌─────────────┐
                  │ Quarantine  │
                  │ (bad data)  │
                  └─────────────┘

Reconciliation runs independently:
  Connector snapshot ──► Merkle diff ──► drift alerts
```

Key design decisions:

- **LEA is the tenant boundary.** Every entity, query, cache key, and log line is scoped to `lea_id`.
- **Append-only snapshots.** Canonical entities are current-state; snapshots are the immutable audit trail. Revert restores prior snapshots.
- **Sync jobs are transactional per page.** One `EventPage` from the connector = one database transaction. Cursor advances only on commit.
- **Operator auth via JWT.** Role-based access (owner, admin, operator, auditor) with per-LEA grants.

## Deployment target

The POC runs locally against real PostgreSQL with four Azure services mocked behind protocols (`infrastructure/azure_mocks/`). The production target is Microsoft Azure: Azure Functions (Flex Consumption) for the poll, reconciliation, sync, and webhook workers; App Service for the FastAPI admin API; Static Web Apps for the SPA; Service Bus Standard (sessions keyed on `lea_id`) for the ingest queue; PostgreSQL Flexible Server with zone-redundant HA; and Key Vault plus managed identity for secrets. Each mock swaps to its real Azure client behind an existing protocol, so business logic does not move.

## Tech stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.13, FastAPI, SQLAlchemy 2.x (async), Pydantic v2 |
| Database | PostgreSQL 16 |
| Migrations | Alembic |
| Frontend | React 19, Chakra UI v3, TanStack Query v5, React Router v7 |
| Testing | pytest + pytest-asyncio, Vitest + RTL + MSW, Playwright |
| Linting | ruff, mypy (strict), TypeScript strict |

## Getting started

### Prerequisites

- Python 3.13+
- Node.js 20+
- Docker (for PostgreSQL)

### Setup

```bash
# 1. Clone and install Python dependencies
git clone <repo-url> && cd edlink-rostering
python -m venv .venv && source .venv/bin/activate  # or .venv/Scripts/activate on Windows
pip install -e ".[dev]"

# 2. Copy environment config
cp .env.example .env

# 3. Start PostgreSQL
scripts/db-up.sh

# 4. Run migrations
scripts/migrate-up.sh

# 5. Seed dev data
scripts/seed-dev.sh

# 6. Start the API server
scripts/api-serve.sh

# 7. Start the web dev server (separate terminal)
cd web && npm install && npm run dev
```

The admin dashboard will be at `http://localhost:8001` and the API at `http://localhost:8000`.

### Running tests

```bash
scripts/test.sh           # Python tests (pytest)
scripts/test-web.sh       # Frontend tests (Vitest)
scripts/e2e.sh            # End-to-end tests (Playwright)
scripts/typecheck.sh      # mypy + tsc
scripts/lint.sh           # ruff
```

## Project structure

```
edlink_rostering/
├── api/              FastAPI routes and middleware
│   └── routers/      Route families (leas, syncs, quarantine, alerts, ...)
├── canonical/        Domain models (Student, Enrollment, LEA)
├── cli/              Operator CLI (sync, revert, reconcile, quarantine)
├── connectors/       Connector protocol + EdLink implementation
├── core/             Settings, types, retry, logging
├── dev/              Dev-only seed data and utilities
├── events/           Event envelope and normalization
├── infrastructure/   Database engine, Azure mocks
└── services/         Business logic
    ├── validation/   5-layer validation pipeline
    ├── queries/      Read-side query modules
    └── ...           sync_worker, reconciliation, bulk_load, etc.

web/src/
├── api/              HTTP client
├── components/       UI components (LeaTable, KpiStrip, AlertsBanner, ...)
├── lib/              Utilities (severity, labels, filters)
└── pages/            Route pages (Dashboard, Leas, Connectors, AdminAudit)

alembic/versions/     Database migrations (8 revisions)
architecture/         Connector framework and data model docs
e2e/                  Playwright end-to-end specs
fixtures/             EdLink event fixture files
scripts/              Dev lifecycle shell scripts
tests/                Python test suite
```

## License

MIT. See [LICENSE](LICENSE).

## Contributing

Issues and pull requests welcome. Conventions for the codebase live under `.claude/rules/` and `CLAUDE.md`; read the relevant rule before opening a PR that touches that area.
