#!/usr/bin/env bash
# Run the end-to-end EdLink rostering POC demo.
#
# Walks through cursor bootstrap, two-batch sync, validation, CLI surface,
# and soft-delete revert. See demo/run.py for the narration.

source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

python -m demo.run "$@"
