#!/usr/bin/env bash
# Wipe Postgres data, restart the container, and re-run all migrations.
# Use when you want a known-clean schema from a known-clean state.

source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

docker compose down -v
docker compose up -d

echo "Waiting for Postgres on localhost:${PORT_POSTGRES:-5433}..."
until docker compose exec -T postgres pg_isready -U postgres -d edlink_poc >/dev/null 2>&1; do
  sleep 1
done

python -m alembic upgrade head
echo
echo "Reset complete. Schema at head."
