#!/usr/bin/env bash
# One command to a running demo: database, schema, seed data, API, and
# admin UI. Idempotent at every step so it is safe to re-run when some
# or all services are already up.
#
#   bash scripts/dev-start.sh        # cold start or resume
#   bash scripts/dev-start.sh --open # same, then open browser
#
# What it does, in order:
#   1. Starts postgres container       (no-op if already running)
#   2. Runs alembic upgrade head       (no-op if at head)
#   3. Seeds five demo LEAs            (idempotent upserts)
#   4. Starts FastAPI on :PORT_API     (skip if port already bound)
#   5. Starts Vite on :PORT_WEB        (foreground; skip if port bound)
#
# The API runs in the background so you see Vite's hot-reload output
# in the terminal. To stop everything, Ctrl+C (kills Vite), then
# the background API process dies with the shell.
#
# To restart the API without stopping Vite: scripts/api-restart.sh
# in another terminal.

source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

open_browser=false
for arg in "$@"; do
  case "$arg" in
    --open) open_browser=true ;;
  esac
done

echo "=== dev-start: postgres ==="
bash scripts/db-up.sh

echo ""
echo "=== dev-start: migrations ==="
bash scripts/migrate-up.sh

echo ""
echo "=== dev-start: seed data ==="
bash scripts/seed-dev.sh

echo ""
echo "=== dev-start: API server ==="
api_pid="$(port_in_use "${PORT_API}")"
if [[ -n "${api_pid}" ]]; then
  echo "API already running on :${PORT_API} (PID ${api_pid}), skipping."
else
  echo "Starting FastAPI on :${PORT_API} (background)..."
  python -m edlink_rostering.api --host 127.0.0.1 --port "${PORT_API}" &
  api_bg_pid=$!

  # Wait briefly for the server to bind before starting Vite, so the
  # proxy target is reachable immediately.
  for i in 1 2 3 4 5 6 7 8 9 10; do
    if port_in_use "${PORT_API}" >/dev/null 2>&1; then
      break
    fi
    sleep 0.5
  done

  if ! port_in_use "${PORT_API}" >/dev/null 2>&1; then
    echo "Warning: API did not bind to :${PORT_API} within 5 seconds." >&2
  else
    echo "API ready on http://127.0.0.1:${PORT_API}"
  fi
fi

echo ""
echo "=== dev-start: admin UI ==="
web_pid="$(port_in_use "${PORT_WEB}")"
if [[ -n "${web_pid}" ]]; then
  echo "Vite already running on :${PORT_WEB} (PID ${web_pid}), skipping."
  echo ""
  echo "Everything is already up:"
  echo "  Admin UI: http://localhost:${PORT_WEB}"
  echo "  API docs: http://127.0.0.1:${PORT_API}/docs"
  if [[ "${open_browser}" == true ]]; then
    start "http://localhost:${PORT_WEB}"
  fi
else
  echo "Starting Vite on :${PORT_WEB} (foreground)..."
  echo ""
  echo "  Admin UI: http://localhost:${PORT_WEB}"
  echo "  API docs: http://127.0.0.1:${PORT_API}/docs"
  echo ""
  echo "  Ctrl+C to stop."
  echo ""

  if [[ "${open_browser}" == true ]]; then
    # Open browser after a brief delay so Vite has time to bind.
    (sleep 3 && start "http://localhost:${PORT_WEB}") &
  fi

  cd web
  export EDLINK_PORT_API="${PORT_API}"
  export EDLINK_PORT_WEB="${PORT_WEB}"
  npm run dev
fi
