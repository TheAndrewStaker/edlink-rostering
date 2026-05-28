#!/usr/bin/env bash
# Run the Playwright suite headed with slow-mo so a human can watch
# each action land in the browser. Default slow-mo is 400ms per action;
# override with PLAYWRIGHT_SLOWMO_MS=<n> for a faster or slower beat.
#
# Setting PLAYWRIGHT_HEADED=1 here flips two things in playwright.config.ts:
#   - launchOptions.slowMo is non-zero (visible pause between actions)
#   - trace and video are recorded for every spec (not just on failure)
# Run `npm run show-report` from e2e/ to view the HTML report after.

source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

export PLAYWRIGHT_HEADED=1
cd e2e
npm run test:headed -- "$@"
