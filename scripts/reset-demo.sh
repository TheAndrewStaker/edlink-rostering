#!/usr/bin/env bash
#
# Reset the EdLink rostering demo LEA (lea-test-001) back to an empty
# state so the demo runner is replayable. Wipes every row that touches
# lea-test-001 in FK-safe order, leaving the seeded LEAs from
# scripts/seed-dev.sh untouched.
#
# What this does NOT touch:
#   - The five seeded LEAs (lea-lakewood-usd, lea-northridge-sd,
#     lea-valley-charter, lea-hillcrest-usd, lea-riverside-usd).
#   - Migrations, Postgres roles, Keycloak (when added), Vault secrets.
#   - The dev stack's Docker container or Kafka topics.
#
# Why a script and not a seed re-run: the demo runner is idempotent on
# its own LEA but a stuck or partial demo state is faster to clear
# explicitly than to root-cause. This script is the wipe.
#
# Usage:
#   bash scripts/reset-demo.sh

set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

DEMO_LEA="lea-test-001"

PG_CONTAINER=$(docker ps --filter "ancestor=postgres:16" --format '{{.Names}}' | head -n 1)
if [[ -z "$PG_CONTAINER" ]]; then
    echo "No running postgres:16 container found. Run: bash scripts/db-up.sh" >&2
    exit 1
fi

echo "Wiping demo LEA $DEMO_LEA via container $PG_CONTAINER ..."

docker exec -i "$PG_CONTAINER" psql -U postgres -d edlink_poc -v ON_ERROR_STOP=1 <<SQL
BEGIN;

-- Audit children of sync_jobs.
DELETE FROM sync_validation_results
 WHERE sync_job_id IN (SELECT id FROM sync_jobs WHERE lea_id = '${DEMO_LEA}');
DELETE FROM revert_actions
 WHERE sync_job_id IN (SELECT id FROM sync_jobs WHERE lea_id = '${DEMO_LEA}');
DELETE FROM retry_actions
 WHERE sync_job_id IN (SELECT id FROM sync_jobs WHERE lea_id = '${DEMO_LEA}');
DELETE FROM quarantine WHERE lea_id = '${DEMO_LEA}';

-- Snapshots.
DELETE FROM student_snapshots WHERE lea_id = '${DEMO_LEA}';
DELETE FROM enrollment_snapshots WHERE lea_id = '${DEMO_LEA}';
DELETE FROM lea_snapshots WHERE lea_id = '${DEMO_LEA}';

-- Canonical + operational.
DELETE FROM enrollments WHERE lea_id = '${DEMO_LEA}';
DELETE FROM students WHERE lea_id = '${DEMO_LEA}';
DELETE FROM sync_jobs WHERE lea_id = '${DEMO_LEA}';
DELETE FROM cursor_state WHERE lea_id = '${DEMO_LEA}';
DELETE FROM leas WHERE id = '${DEMO_LEA}';

COMMIT;

\echo
\echo Demo LEA reset. Counts after wipe (all 0):
SELECT 'students'    AS tbl, COUNT(*) FROM students     WHERE lea_id = '${DEMO_LEA}'
UNION ALL SELECT 'enrollments',  COUNT(*) FROM enrollments  WHERE lea_id = '${DEMO_LEA}'
UNION ALL SELECT 'sync_jobs',    COUNT(*) FROM sync_jobs    WHERE lea_id = '${DEMO_LEA}'
UNION ALL SELECT 'quarantine',   COUNT(*) FROM quarantine   WHERE lea_id = '${DEMO_LEA}'
UNION ALL SELECT 'cursor_state', COUNT(*) FROM cursor_state WHERE lea_id = '${DEMO_LEA}';
SQL

echo "Done. bash scripts/demo.sh will replay end-to-end."
