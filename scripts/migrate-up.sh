#!/usr/bin/env bash
# Apply all pending Alembic migrations.

source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

python -m alembic upgrade head
