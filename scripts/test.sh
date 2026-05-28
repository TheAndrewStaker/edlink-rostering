#!/usr/bin/env bash
# Run the pytest suite.

source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

python -m pytest -v "$@"
