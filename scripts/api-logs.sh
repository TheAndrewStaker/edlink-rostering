#!/usr/bin/env bash
#
# api-logs.sh — tail the FastAPI dev server log
#
# api-serve.sh tees uvicorn's stdout+stderr to var/logs/api.log so the
# full request log and any unhandled-exception traceback survive after
# the serving terminal scrolls. This tails that file.
#
# Unhandled 500s are ALSO captured structurally (one JSON record per
# exception, with the traceback) by the telemetry FileSink at
# var/logs/app_insights.jsonl, written by the catch-all handler in
# edlink_rostering/api/errors.py. Use that file when you want just the
# exceptions; use this one for the full request stream.
#
# Usage:
#   ./scripts/api-logs.sh
#
# Environment:
#   .env (sourced via _lib.sh)

set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

log_file="var/logs/api.log"
if [[ ! -f "${log_file}" ]]; then
  echo "No ${log_file} yet. Start the API with scripts/api-serve.sh first." >&2
  exit 1
fi

exec tail -n 200 -f "${log_file}"
