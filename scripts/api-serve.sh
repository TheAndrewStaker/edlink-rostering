#!/usr/bin/env bash
# Start the FastAPI admin server.
#
# Binds to 127.0.0.1:${PORT_API} (PORT_API is derived from
# EDLINK_PORT_BASE in _lib.sh). The React app at web/
# proxies /api requests to the same port so the two stay locked.
#
# Refuses to start if the port is already bound to avoid serving
# stale code from a forgotten background process. The "stale process
# on :8000 serving code from before my last refactor" footgun has
# eaten roughly an hour of debugging per quarter; the guard kills it.
# When the guard fires, run scripts/api-restart.sh to kill and rebind,
# or kill the owning PID yourself.
#
# Reload mode is off by default because uvicorn's reload watcher on
# Windows orphans its multiprocessing child when the parent dies,
# leaving stale code serving the port. Pass --reload yourself if you
# want it.

source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

owner_pid="$(port_in_use "${PORT_API}")"
if [[ -n "${owner_pid}" ]]; then
  echo "Port ${PORT_API} is already bound by PID ${owner_pid}." >&2
  echo "  - run scripts/api-restart.sh to replace the running server" >&2
  echo "  - or kill ${owner_pid} yourself if you want a clean start" >&2
  echo "  - or set EDLINK_PORT_BASE in .env to a different number" >&2
  exit 1
fi

# Tee stdout+stderr to a durable log so the request log and any
# unhandled-exception traceback survive after the serving terminal
# scrolls. scripts/api-logs.sh tails this file. Unhandled 500s are also
# captured structurally by the telemetry FileSink at
# var/logs/app_insights.jsonl (see the catch-all handler in
# edlink_rostering/api/errors.py).
mkdir -p var/logs
python -m edlink_rostering.api --host 127.0.0.1 --port "${PORT_API}" "$@" 2>&1 \
  | tee -a var/logs/api.log
