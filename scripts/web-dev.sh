#!/usr/bin/env bash
# Start the Vite dev server for the Chakra admin app.
#
# Binds to :${PORT_WEB} and proxies /api to http://127.0.0.1:${PORT_API}
# (the FastAPI server started by scripts/api-serve.sh). Both ports
# come from _lib.sh's EDLINK_PORT_BASE so the two stay locked.
# Run both together for the full admin demo:
#
#   Terminal 1: bash scripts/api-serve.sh
#   Terminal 2: bash scripts/web-dev.sh

source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

cd web
# Export the API target so vite.config.ts can read it via process.env
# at config load. Vite already inherits the parent shell's env; this
# is just to make the dependency visible at the script layer.
export EDLINK_PORT_API="${PORT_API}"
export EDLINK_PORT_WEB="${PORT_WEB}"
npm run dev -- "$@"
