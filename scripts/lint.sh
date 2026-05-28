#!/usr/bin/env bash
# Run ruff lint + format check across the edlink_rostering package and tests.
#
# Both gates fail loud: a lint violation or a format mismatch exits
# non-zero so CI / IntelliJ run configs catch the regression rather
# than letting it ship. Config lives in pyproject.toml under
# [tool.ruff].

source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

python -m ruff check edlink_rostering tests
python -m ruff format --check edlink_rostering tests
