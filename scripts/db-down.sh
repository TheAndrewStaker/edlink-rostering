#!/usr/bin/env bash
# Stop the local Postgres container. Data persists in the postgres_data volume.

source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

docker compose down
