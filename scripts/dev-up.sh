#!/usr/bin/env bash
# Spin a presentable dev stack from scratch: container, schema, and
# seeded LEA state. Composes the three focused scripts so a fresh
# clone is one command from a walkthrough-ready admin app.
#
# Equivalent to running, in order:
#   1. bash scripts/db-up.sh         (postgres container)
#   2. bash scripts/migrate-up.sh    (alembic head)
#   3. bash scripts/seed-dev.sh      (five demo LEAs)
#
# Each underlying script stays single-purpose; this one is the
# composition. Use it when you do not need to think about which step
# is missing. Use the individual scripts when you do.

set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

bash scripts/db-up.sh
bash scripts/migrate-up.sh
bash scripts/seed-dev.sh

echo ""
echo "Dev stack ready. Next steps:"
echo "  bash scripts/api-serve.sh   # FastAPI on :${PORT_API}"
echo "  bash scripts/web-dev.sh     # Chakra admin on :${PORT_WEB}"
echo ""
echo "Or use scripts/dev-start.sh to start everything in one command."
