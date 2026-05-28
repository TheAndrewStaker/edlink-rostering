---
paths:
  - web/src/**/*.tsx
  - web/src/**/*.ts
---

# Frontend copy discipline

Never render a raw backend code, enum, slug, or opaque identifier as
user-facing text. Operators read this app to triage incidents; every
character on screen is either readable English, an intentional
mono-spaced technical hint, or a tooltip-revealed detail. The default
in this codebase is **translate at the boundary**, not "let the
component render whatever the API returned."

## What counts as a "code" or "id"

If any of these appear directly in `<Text>`, `<Badge>`, `<Tag>`,
table cells, headings, dialog titles, helper text, error messages,
empty-state strings, or tooltip labels, they need translation:

- Enum values: `critical`, `warning`, `failed`, `success`, `revert`,
  `quarantine_release`, `traditional_district`, `charter_lea`,
  `boces`, `edlink`, `ednition`, `clever`, `oneroster`, `edfi`.
- Layer / error codes: `L2:SCHEMA_MISSING_FIELD@evt_met_010`,
  `HTTP_INTEGRITY_FAILED`, `ENROLLMENT_ORPHAN_STUDENT`,
  `THRESHOLD_POPULATION_SHIFT`, anything that looks like
  `[A-Z_]{6,}`.
- Opaque event ids: `evt_val_post`, `evt_nrd_005_after`,
  `evt_lkw_001`. Any string matching `evt_[a-z0-9_]+` or any cursor
  string longer than ~14 chars is an opaque identifier.
- Raw record ids: `lea-valley-charter`, `enr-hcr-orph-001`,
  UUIDs. The user knows these as the LEA's display name + state +
  type, not the slug.
- Database column names: `lea_id`, `student_id`, `cursor_before`,
  `last_event_at`. Use prose ("LEA", "student", "Before", "Last
  event") in headers and bodies.

The rule is not "hide them" — it's "translate them." Many of these
values are still useful to an operator with the developer console
open or a support engineer pasting an incident into a ticket. Keep
them accessible **via tooltip** (`title="..."` on the element, or a
Chakra `Tooltip.Root`), but never as the primary rendered string.

## Where the translation lives

`web/src/lib/labels.ts` is the single home for display
maps. It exports:

- `labelForSeverity(level)` → "Critical" / "Warning" / "Stale" /
  "Healthy"
- `labelForPartner(partner)` → "EdLink" / "Ednition" / "Ed-Fi" /
  "OneRoster" / "Clever"
- `labelForSyncStatus(status)` → "Success" / "Failed" / "Reverted" /
  "Quarantine release" / "Running"
- `labelForLeaType(type)` → "Traditional district" / "Charter LEA" /
  "Charter CMO" / "BOCES" / "State agency"
- `labelForErrorCode(code)` → human label for layer-1 through
  layer-5 codes
- `summarizeErrorSummary(raw)` → parses
  `L<n>:CODE[@<event_id>]; ...` into plain English

When adding a new backend enum, add the mapping at the same time as
the schema change. The maps fall back to a sentence-cased rendering
of the raw value, so an unmapped code degrades gracefully rather than
shouting at the user, but **a fallback is not a substitute for an
explicit mapping**. The PR description for any backend enum change
should mention the matching label entry.

## Where IDs and codes go when they survive

Opaque identifiers are valuable to operators in the right context.
The patterns this codebase uses:

1. **Tooltip on the human label.** Put the raw id in the `title` of
   the `<Text>` or `<Box>` that holds the human label. Hover reveals
   the slug.
2. **Compact + tooltip.** For long opaque cursors, render a
   `evt_xx…1234` compact form via the `compact()` helper in
   `LeaDetailPanel.tsx` (or extract to `lib/format.ts` when a second
   call site appears), and put the full value in the tooltip.
3. **Mono-spaced detail line, smaller.** A sync_job's UUID under its
   started-at time is acceptable as `fontFamily="mono"` text at
   `fontSize="xs" color="gray.500"` because the operator pastes it
   into the CLI. The font-family is the signal: "this is a developer
   handle, not prose."

Pattern 1 is the default. Pattern 2 is for fields that will appear
across many rows and benefit from a fixed visual width. Pattern 3 is
the exception, reserved for fields the operator copy-pastes into
other tools.

## Header copy

Section headers and table column headers describe **what the column
represents**, not the database column name. Examples:

- Heading text: "Local Education Agencies" not "LEAs", "Sync
  timeline" not "Sync jobs", "Quarantine queue" not "Quarantine".
  Spell out acronyms in the first prominent label.
- Column headers: "Latest sync", "Cursor lag", "Students",
  "Enrollments", "Partner", "Severity". Avoid "lea_id" or "status"
  as a column header.
- Section subtitles add the dimension the operator needs: "Sorted by
  severity", "Districts, charter LEAs, and CMOs under management",
  "Click outside to close".

## Empty states and error messages

Empty states explain the cause and the resolution in one sentence:

```
✗ "No data."
✓ "No quarantined rows for this LEA. Layer 4 routes orphan
   enrollments here; an empty queue means referential checks are
   clean."

✗ "Loading..."
✓ Use the Chakra <Skeleton> component shaped like the eventual row.

✗ "Failed."
✓ "Could not load alerts. The admin API may be restarting; the
   dashboard will retry automatically."
```

Error messages from `ApiError` are already typed; don't render
`.message` directly when the backend returns a code. Translate at the
boundary, like the labels above.

## Tabular numbers

Number columns (counts, lag days, durations) use
`fontVariantNumeric="tabular-nums"` so digits line up vertically
across rows. Header and data alignment match: numeric columns use
`textAlign="end"`. Categorical columns left-align by default.

## What to do when editing

Whenever you touch a `.tsx` or `.ts` file under `web/src/`:

- Read the rendered text. If it contains an underscore, an
  ALL_CAPS_TOKEN, a slug, or a cursor that looks like
  `evt_*`/`abc12345`, run it through `lib/labels.ts` first.
- If the value is new and `lib/labels.ts` does not have a mapping,
  add one. Do not add the value to a component as a one-off.
- Tooltips are not optional for opaque identifiers; they're how the
  raw value stays reachable.
- When in doubt, type the value as prose and ask: "would a district
  ops lead recognize this without context?" If not, translate it.

## Automated check

`scripts/check-enum-leaks.sh` greps `web/src/**/*.tsx` for
JSX expressions like `{x.role}`, `{x.status}`, `{x.severity}`,
`{x.partner}`, `{x.lea_type}`, `{x.kind}`, `{x.section}`,
`{x.source}`, `{x.action}`. It is wired into `scripts/typecheck.sh`
so any unwrapped enum render fails the gate. Tooltip (`title={...}`),
React key (`key={...}`), template-literal interpolation (`${...}`),
and lines already calling `labelFor*` are excluded.

If you have a legitimate need to render the raw value (rare; almost
always wrong), append the comment `// enum-leak: ok <reason>` to
that line and the checker will skip it.

When you add a new backend enum, do all three in the same change:

1. Define the field in `edlink_rostering/api/schemas.py` (or wherever the
   wire type lives).
2. Add the matching map + helper in `web/src/lib/labels.ts`.
3. If the enum surfaces in a new place, update the
   `ENUM_FIELDS` regex in `check-enum-leaks.sh` so the gate covers it.

## Cross-references

- `web/src/lib/labels.ts` — the display-map module
- `scripts/check-enum-leaks.sh` — the gate script
- `.claude/rules/copy-style.md` — universal copy rules (no
  em-dashes, no AI-tell phrases). This file is the frontend
  amplification.
- `web/src/components/LeaTable.tsx`,
  `LeaDetailPanel.tsx` — canonical examples of the translation
  pattern in action.
