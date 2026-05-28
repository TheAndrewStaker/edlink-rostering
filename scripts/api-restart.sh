#!/usr/bin/env bash
# Kill any stale uvicorn listener on :${PORT_API}, then start a fresh
# one.
#
# Orphaned api-serve processes are the #1 source of "the admin app is
# 404ing on routes I just added." It happens when a Git Bash terminal
# is closed via the X button rather than Ctrl+C: bash dies, the python
# child keeps running, the socket stays bound, and the next
# `api-serve.sh` either silently fails to bind or serves stale code.
#
# This script is the one-click "kill and restart" entry point. The
# IntelliJ run config "API: Restart" wires to it. ``api-serve.sh``
# itself refuses to start when the port is already bound, so a forced
# restart goes through here.

source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

# Use PowerShell to find the PID owning :${PORT_API} and kill it.
# Net-TCP cmdlets are the most reliable on modern Windows; netstat
# parsing works too but is brittle across locales. Stop-Process
# -Force kills without waiting; we then sleep briefly so the socket
# release races the uvicorn rebind.
echo "Looking for processes bound to 127.0.0.1:${PORT_API}..."
powershell.exe -NoProfile -Command "
  \$conns = Get-NetTCPConnection -LocalPort ${PORT_API} -ErrorAction SilentlyContinue
  if (\$conns) {
    \$pids = \$conns | Select-Object -ExpandProperty OwningProcess -Unique
    foreach (\$procId in \$pids) {
      try {
        \$p = Get-Process -Id \$procId -ErrorAction Stop
        Write-Host \"Stopping PID \$procId (\$(\$p.ProcessName))\"
        Stop-Process -Id \$procId -Force -ErrorAction Stop
      } catch {
        Write-Host \"PID \$procId already gone\"
      }
    }
  } else {
    Write-Host 'No listener on :${PORT_API}.'
  }
" || true

# Brief pause so the OS releases the TCP socket before uvicorn binds.
# Windows holds the socket in TIME_WAIT briefly; 500ms is enough to
# avoid a race on the rebind.
sleep 0.5

exec "$(dirname "${BASH_SOURCE[0]}")/api-serve.sh" "$@"
