#!/usr/bin/env bash
# Run the Playwright end-to-end suite headless.
#
# Playwright owns the API + Vite lifecycle via webServer entries in
# playwright.config.ts. Per-spec DB reset is configured in the shared
# fixture at e2e/fixtures/test-base.ts.

source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

cd e2e
npm run test -- "$@"
