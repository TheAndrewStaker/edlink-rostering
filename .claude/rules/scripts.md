---
paths:
  - scripts/**/*
  - api/scripts/**/*
  - justfile
  - Makefile
---

# Scripts and dev lifecycle

Conventions for development scripts. Python backend uses `uv` (or `poetry`); frontend uses `npm` or `pnpm`. Top-level scripts orchestrate.

## Top-level entry points

| Command | What it does |
|---|---|
| `just start-dev` or `./scripts/start-dev.sh` | Start full dev stack (DB, Redis, API, frontend) |
| `just stop-dev` or `./scripts/stop-dev.sh` | Stop dev stack |
| `just check-dev` or `./scripts/check-dev.sh` | Verify dev stack is healthy |
| `just test` | Run all tests |
| `just test-unit` | Unit tests only |
| `just test-integration` | Integration tests (require DB) |
| `just test-e2e` | Playwright e2e tests |
| `just lint` | Ruff + mypy |
| `just fmt` | Ruff format |
| `just seed` | Run seed data on dev DB |
| `just reset` | Reset all dev state to known seed |

Either `just` (Justfile) or a bash script entry point — pick one and standardize.

## Service lifecycle

The dev stack typically includes:

- PostgreSQL (via Docker or local install)
- Redis (via Docker or local install)
- API (FastAPI via uvicorn)
- Frontend (Vite or Next dev server)
- Optional: connector mocks (for offline development)

Each service has a known port; ports are stable (configured via `.env`):

```
.env (committed example as .env.example)
EDLINK_API_PORT=8000
EDLINK_WEB_PORT=5173
EDLINK_DB_PORT=5432
EDLINK_REDIS_PORT=6379
EDLINK_E2E_API_PORT=8100   # e2e profile, separate from dev
EDLINK_E2E_DB_PORT=5532
```

Default ports are stable across machines. If conflicts occur, override per-developer in `.env` (gitignored).

## Process management

For development, use `tmux` panes, `process-compose`, `overmind`, or similar — not background jobs. Background jobs orphan processes and leave dev stacks in inconsistent states.

```bash
# scripts/start-dev.sh
set -euo pipefail

# Ensure infrastructure
docker compose -f docker-compose.dev.yml up -d postgres redis

# Wait for DB
./scripts/wait-for-db.sh

# Run migrations
cd api && uv run alembic upgrade head && cd ..

# Run seed
cd api && uv run python -m edlink_rostering.scripts.seed && cd ..

# Start services in process-compose (Procfile-style)
process-compose up -f process-compose.yml
```

`process-compose.yml` describes the API and frontend processes; `process-compose` brings them up together and provides a unified view.

## Health check before declaring "started"

`start-dev.sh` doesn't return until all services pass health checks:

```bash
# Wait for API
for i in {1..30}; do
    if curl -sf http://localhost:$EDLINK_API_PORT/health > /dev/null; then
        echo "API ready"
        break
    fi
    sleep 1
done
```

Stale health implies misconfigured startup or a service that died silently. Better to fail loud at script time than discover later.

## E2E profile separate from dev

E2E tests use a separate environment to avoid stepping on dev data:

```
# .env.e2e
EDLINK_API_PORT=8100
EDLINK_DB_PORT=5532
EDLINK_REDIS_PORT=6479
APP_ENV=test
```

```bash
# scripts/start-e2e.sh
APP_ENV=test docker compose -f docker-compose.e2e.yml up -d
cd api && APP_ENV=test uv run alembic upgrade head
cd api && APP_ENV=test uv run python -m edlink_rostering.scripts.seed_e2e
cd api && APP_ENV=test uv run uvicorn edlink_rostering.main:app --port $EDLINK_API_PORT
```

E2E tests run against this profile. CI uses the same scripts.

## Reset scripts per persona

Per `.claude/rules/seed-data.md`, reset scripts wipe just enough to re-test one flow:

```
scripts/reset/
├── reset-demo.sh                   # Full reset (uses just reset)
├── reset-alice-iep.sh              # Just Alice's IEP
├── reset-bob-evaluation.sh         # Just Bob's evaluation scenario
└── reset-roster-sync.sh            # Roster data only
```

Each reset script has a header comment listing tables/rows it touches.

## DevTools reloading

API uses `uvicorn --reload` in dev. Frontend uses Vite HMR. **Both are first-class.**

Watch directories should exclude:

- `api/__pycache__`
- `node_modules`
- `.venv`
- `dist`
- `*.log`
- migration files (auto-reload during migration write churns the process)

```bash
uv run uvicorn edlink_rostering.main:app \
    --reload \
    --reload-dir edlink_rostering \
    --reload-exclude '**/__pycache__/**' \
    --reload-exclude '**/alembic/versions/**' \
    --port $EDLINK_API_PORT
```

## Worktree management

For parallel feature branches, use git worktrees:

```bash
# scripts/worktree-create.sh feature-x
git worktree add ../edlink-feature-x feature-x
cd ../edlink-feature-x
./scripts/start-dev.sh  # uses .env.feature-x with non-default ports
```

Each worktree uses non-default ports so multiple are runnable simultaneously. Pattern: derive port offsets from worktree name hash.

## Stale stack cleanup

`scripts/clean-stale-stacks.sh` finds and kills orphaned processes from dead worktrees or crashed dev sessions:

```bash
# Kill processes on dev ports that aren't from current worktree
lsof -ti :$EDLINK_API_PORT | grep -v $(pgrep -f "uvicorn.*$(pwd)") | xargs -r kill -9
```

Run weekly or when "address already in use" appears.

## Sprint runner — not in this project

Multi-agent workflow configuration for the application lives outside this bundle.

## CI parity

The same scripts that work locally work in CI:

```yaml
# .github/workflows/test.yml or equivalent
- run: ./scripts/start-dev.sh
- run: just test-unit
- run: just test-integration
- run: just test-e2e
- run: ./scripts/stop-dev.sh
```

Don't have CI-specific commands. If something needs to differ between local and CI, parameterize via env vars.

## Permissions

All scripts:

- Are `chmod +x` and have shebang lines
- Use `set -euo pipefail` at the top
- Quote variables properly
- Don't `sudo` (require dev environment with appropriate perms)
- Don't depend on side effects of previous scripts (composable)

## Documentation

Each script has a header comment:

```bash
#!/usr/bin/env bash
#
# start-dev.sh — start the development stack
#
# Brings up Postgres, Redis, API, and frontend on the default dev ports.
# Runs migrations and seed data before starting application services.
#
# Usage:
#   ./scripts/start-dev.sh
#
# Environment:
#   .env (sourced if present)
#
# Side effects:
#   - Starts Docker containers for postgres and redis
#   - Runs alembic upgrade on the dev DB
#   - Seeds dev data (idempotent)
#   - Starts uvicorn and vite via process-compose

set -euo pipefail
```

## Cross-references

- `.claude/rules/alembic.md` — migrations
- `.claude/rules/seed-data.md` — seed pattern
- `.claude/rules/dependencies.md` — package management
- `docs/STACK_REFERENCES.md` — tool versions
