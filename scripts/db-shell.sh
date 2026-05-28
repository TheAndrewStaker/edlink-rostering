#!/usr/bin/env bash
# Open an interactive psql shell against the local Postgres container.
# Convenience for poking at tables, inspecting grants, running ad-hoc SQL.

source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

docker compose exec postgres psql -U postgres -d edlink_poc
