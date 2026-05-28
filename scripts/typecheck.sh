#!/usr/bin/env bash
# Run mypy across the edlink_rostering package, then the JSX enum-leak check
# against web/src.
#
# Both gates fail loud: a mypy error or an unwrapped enum render
# exits non-zero so CI / IntelliJ run configs catch the regression
# rather than letting it ship.
#
# Resolve the absolute path of the sibling check-enum-leaks.sh BEFORE
# sourcing _lib.sh. _lib.sh cd's to the project root, after which a
# relative BASH_SOURCE-derived path no longer points at the right place
# when the script was invoked from a different directory (e.g. from
# a parent as `bash scripts/typecheck.sh`).

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
enum_check="${script_dir}/check-enum-leaks.sh"

source "${script_dir}/_lib.sh"

python -m mypy edlink_rostering

bash "${enum_check}"
