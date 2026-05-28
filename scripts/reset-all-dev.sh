#!/usr/bin/env bash
#
# Nuke every LEA in the dev database and rebuild from scratch via
# scripts/seed-dev.sh. Use when the dev stack is in an unknown state
# and you want a clean baseline before a walkthrough.
#
# What this does:
#   - DELETEs everything in dependency order: audit children of
#     sync_jobs, snapshots, canonical, sync_jobs, cursor_state, leas.
#   - Re-runs the seed module so the five demo LEAs come back.
#
# What this does NOT touch:
#   - Migrations (Postgres schema stays at head).
#   - Postgres roles.
#   - The container itself or its volume.
#
# Usage:
#   bash scripts/reset-all-dev.sh

set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

PG_CONTAINER=$(docker ps --filter "ancestor=postgres:16" --format '{{.Names}}' | head -n 1)
if [[ -z "$PG_CONTAINER" ]]; then
    echo "No running postgres:16 container found. Run: bash scripts/db-up.sh" >&2
    exit 1
fi

echo ""
echo "  Full dev reset"
echo "  This deletes every LEA, sync_job, snapshot, and audit row in"
echo "  the dev database, then re-seeds the five demo LEAs."
echo ""
read -r -p "  Proceed? [y/N] " CONFIRM
if [[ "$CONFIRM" != "y" && "$CONFIRM" != "Y" ]]; then
    echo "  Aborted."
    exit 0
fi

echo "Wiping all tenant data via container $PG_CONTAINER ..."
docker exec -i "$PG_CONTAINER" psql -U postgres -d edlink_poc -v ON_ERROR_STOP=1 <<'SQL'
BEGIN;
DELETE FROM sync_validation_results;
DELETE FROM revert_actions;
DELETE FROM retry_actions;
DELETE FROM quarantine;
DELETE FROM student_snapshots;
DELETE FROM enrollment_snapshots;
DELETE FROM lea_snapshots;
DELETE FROM enrollments;
DELETE FROM students;
DELETE FROM sync_jobs;
DELETE FROM cursor_state;
DELETE FROM leas;
COMMIT;
\echo
\echo Clean. Row counts (all 0):
SELECT 'leas' AS tbl, COUNT(*) FROM leas
UNION ALL SELECT 'students',    COUNT(*) FROM students
UNION ALL SELECT 'enrollments', COUNT(*) FROM enrollments
UNION ALL SELECT 'sync_jobs',   COUNT(*) FROM sync_jobs
UNION ALL SELECT 'quarantine',  COUNT(*) FROM quarantine;
SQL

echo "Re-seeding the five demo LEAs ..."
python -m edlink_rostering.dev.seed

echo "Done. bash scripts/api-serve.sh and open the admin app."
