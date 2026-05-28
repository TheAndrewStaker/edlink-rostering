#!/usr/bin/env bash
# Show the current migration head and the full revision history.

source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

echo "── current ──────────────────────────────────────────"
python -m alembic current
echo
echo "── history ──────────────────────────────────────────"
python -m alembic history
