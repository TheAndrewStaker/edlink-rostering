#!/usr/bin/env bash
#
# Reset just the quarantine queue so the founder can re-test the
# release/reject flow without re-seeding the rest of the dev stack.
# Restores the seeded Hillcrest USD quarantine backlog from
# edlink_rostering.dev.seed.
#
# What this does:
#   - DELETEs every row from `quarantine` (resolved + unresolved).
#   - Re-runs the seed module to rebuild the 30-row Hillcrest USD
#     backlog from deterministic UUIDs.
#
# What this does NOT touch:
#   - Canonical rows (LEAs, students, enrollments).
#   - sync_jobs, sync_validation_results, revert_actions, retry_actions.
#   - Cursor state.
#
# Usage:
#   bash scripts/reset-quarantine.sh

set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

PG_CONTAINER=$(docker ps --filter "ancestor=postgres:16" --format '{{.Names}}' | head -n 1)
if [[ -z "$PG_CONTAINER" ]]; then
    echo "No running postgres:16 container found. Run: bash scripts/db-up.sh" >&2
    exit 1
fi

echo "Wiping quarantine via container $PG_CONTAINER ..."
docker exec -i "$PG_CONTAINER" psql -U postgres -d edlink_poc -v ON_ERROR_STOP=1 <<'SQL'
BEGIN;
DELETE FROM quarantine;
COMMIT;
\echo
\echo Quarantine cleared.
SQL

echo "Re-running seed module to rebuild the Hillcrest USD backlog ..."
python -m edlink_rostering.dev.seed

echo "Done. bash scripts/api-serve.sh + open the admin app to re-test release/reject."
