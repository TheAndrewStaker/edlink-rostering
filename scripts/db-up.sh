#!/usr/bin/env bash
# Start the local Postgres container in the background and wait until it
# accepts connections.

source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

docker compose up -d

echo "Waiting for Postgres on localhost:${PORT_POSTGRES:-5433}..."
until docker compose exec -T postgres pg_isready -U postgres -d edlink_poc >/dev/null 2>&1; do
  sleep 1
done

echo "Postgres ready."
docker compose ps
