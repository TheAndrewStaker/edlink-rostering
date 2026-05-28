#!/usr/bin/env bash
# Run the Vitest component-test suite for the admin web app.
#
# Default mode is `vitest run` (one-shot, exits when done). For watch
# mode, pass --watch or invoke `npm run test` directly from web/.

source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

cd web
npm run test:run -- "$@"
