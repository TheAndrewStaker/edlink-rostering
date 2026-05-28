#!/usr/bin/env bash
# Wipe the dev database and rebuild a presentable demo state.
#
# Composes db-reset + seed-dev so the operator gets the same starting
# point every time. Use this after a demo that left mutated state you
# do not want to triage one row at a time.
#
# Equivalent to:
#   bash scripts/db-reset.sh    (docker compose down -v + up + alembic head)
#   bash scripts/seed-dev.sh    (five demo LEAs)
#
# Use the underlying scripts directly when you want just one half.

set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

bash scripts/db-reset.sh
bash scripts/seed-dev.sh

echo ""
echo "Dev stack reset and seeded. Five LEAs ready in the admin app."
