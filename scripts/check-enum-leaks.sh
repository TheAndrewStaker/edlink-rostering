#!/usr/bin/env bash
# Flag raw enum / code-like values rendered in JSX without a label
# helper.
#
# What this catches
# -----------------
# Lines under web/src/ where a backend enum is rendered directly as
# visible text, like:
#
#     <Badge>{persona.role}</Badge>
#     <Text>{alert.severity}</Text>
#
# Those should go through lib/labels.ts:
#
#     <Badge>{labelForRole(persona.role)}</Badge>
#     <Text>{labelForSeverity(alert.severity)}</Text>
#
# What this intentionally skips
# -----------------------------
# 1. `title={x.foo}` — the documented "raw value reachable via tooltip"
#    pattern in .claude/rules/frontend-copy.md.
# 2. `key={x.foo}` — React keys are not rendered text.
# 3. `${x.foo}` template-literal interpolation (not JSX text).
# 4. `lib/labels.ts` itself — that is where the mapping lives.
# 5. Any line containing `labelFor` — already wrapped.
# 6. A trailing `// enum-leak: ok <reason>` comment — explicit
#    escape hatch for the rare legitimate case.
#
# Exit code
# ---------
# 0 if clean; 1 if any violations found. Wired into typecheck.sh so a
# regression fails the gate.

set -euo pipefail

# Force a UTF-8 locale so grep -P does not complain on Git Bash on
# Windows, which often sets LANG to a unibyte default.
export LC_ALL="${LC_ALL:-C.UTF-8}"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
prototype_dir="$(cd "${script_dir}/.." && pwd)"
web_src="${prototype_dir}/web/src"

if [[ ! -d "${web_src}" ]]; then
  echo "enum-leak check: ${web_src} does not exist, skipping."
  exit 0
fi

# Fields that, when rendered as bare JSX text, indicate an enum leak.
# Adding a new backend enum means adding it here AND to lib/labels.ts.
ENUM_FIELDS='role|status|severity|partner|lea_type|kind|section|source|action'

# Pattern: `{x.<field>}` where x is a simple identifier. The trailing
# `}` makes us conservative: complex expressions like
# `colorPalette={MAP[x.role]}` do not match because the closing brace
# is not directly after the field.
PATTERN="\\{[a-zA-Z_][a-zA-Z_0-9]*\\.(${ENUM_FIELDS})\\}"

# Initial match list. PCRE2 keeps the regex portable across grep
# implementations. --include scopes to .tsx (JSX text only).
raw_hits=$(
  grep -RPn "${PATTERN}" "${web_src}" --include='*.tsx' || true
)

if [[ -z "${raw_hits}" ]]; then
  echo "enum-leak check: clean."
  exit 0
fi

# Post-filter the documented exclusions.
violations=$(
  printf '%s\n' "${raw_hits}" \
    | grep -v 'title={' \
    | grep -v 'key={' \
    | grep -vP '\$\{[a-zA-Z_]' \
    | grep -v 'labelFor[A-Z]' \
    | grep -v 'enum-leak: ok' \
    | grep -v 'lib/labels.ts' \
    || true
)

if [[ -z "${violations}" ]]; then
  echo "enum-leak check: clean."
  exit 0
fi

echo ""
echo "enum-leak check: FAILED"
echo ""
echo "The following lines render a backend enum as raw JSX text."
echo "Wrap with a labelFor* helper from lib/labels.ts, or add the"
echo "trailing comment '// enum-leak: ok <reason>' if the raw value"
echo "is intentional."
echo ""
echo "Background: .claude/rules/frontend-copy.md"
echo ""
printf '%s\n' "${violations}"
exit 1
