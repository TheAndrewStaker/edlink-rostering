#!/usr/bin/env bash
# Seed the dev Postgres with five realistic LEAs spanning the
# operational states the admin app demos: happy path, recent revert,
# failed sync (drives sync_failure + schema_drift alerts), quarantine
# backlog (drives quarantine_growth alert), and stale cursor (drives
# cursor_lag_20_day alert).
#
# Idempotent: re-running updates date-sensitive rows so the stale
# cursor stays exactly 25 days behind regardless of when this runs.

source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

python -m edlink_rostering.dev.seed
