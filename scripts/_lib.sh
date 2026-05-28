#!/usr/bin/env bash
# Shared shell helpers for prototype scripts.
#
# Every script sources this file. Behavior:
#   - cd to the project root regardless of where the script is invoked
#   - source .env if it exists (real values)
#   - fall back to .env.example if .env is absent (committed defaults)
#   - compute deterministic ports from EDLINK_PORT_BASE so multiple
#     checkouts can coexist by setting different bases per checkout
#
# Exports the relevant env vars to the caller's shell so child processes inherit
# them.

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
prototype_dir="$(cd "${script_dir}/.." && pwd)"

cd "${prototype_dir}"

if [[ -f .env ]]; then
  env_file=".env"
else
  env_file=".env.example"
fi

set -a
# shellcheck disable=SC1090
source "${env_file}"
set +a

# Derived ports. EDLINK_PORT_BASE is the contract; everything below
# is a fixed offset so a "what's on :NNNN?" question has one answer.
# PORT_API is the FastAPI admin server; PORT_WEB is the Vite dev
# server. Vite proxies /api -> http://127.0.0.1:${PORT_API}/api so the
# two ports stay in lockstep through the offset.
: "${EDLINK_PORT_BASE:=8100}"
export EDLINK_PORT_BASE
export PORT_API="${EDLINK_PORT_BASE}"
export PORT_WEB=$((EDLINK_PORT_BASE + 1))

# port_in_use <port> echoes the PID owning the port, or nothing.
# Uses PowerShell's Get-NetTCPConnection because lsof is unavailable
# on Git Bash and netstat parsing is brittle across locales.
port_in_use() {
  local port="$1"
  powershell.exe -NoProfile -Command "
    \$c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if (\$c) { \$c.OwningProcess | Select-Object -First 1 }
  " 2>/dev/null | tr -d '\r\n ' || true
}
