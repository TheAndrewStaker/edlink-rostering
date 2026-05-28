#!/usr/bin/env bash
# Run the pytest suite with coverage reporting.
#
# Requires pytest-cov from [project.optional-dependencies].dev. Coverage is
# kept out of the default `test.sh` so dev-loop pytest runs stay fast; this
# script is the explicit "I want the coverage report" entry point and the
# one CI invokes.

source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

python -m pytest --cov=edlink_rostering --cov-report=term-missing --cov-report=html "$@"
