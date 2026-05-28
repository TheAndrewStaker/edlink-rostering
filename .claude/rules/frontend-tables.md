---
paths:
  - web/src/pages/**/*.tsx
  - web/src/components/**Table*.tsx
  - web/src/components/**Filter*.tsx
  - web/src/lib/leaFilters.ts
---

# Frontend tables

How tables read across the admin app: page header counts, filter bar shape, sort affordances, deep-linkable URL state. Two canonical examples land in the codebase: `pages/Leas.tsx` + `components/LeaTable.tsx` (severity-bucketed master list) and `pages/Integrations.tsx` (cross-LEA integration roll-up). Both surfaces hold to the same shape; new tables that diverge should explain why in the PR description.

This rule auto-loads when editing any page or table component so future tables match the conventions without re-litigation.

## Page header: H1 matches the nav label

The `<h1>` in the page header is exactly the nav label for that page (`Dashboard`, `LEAs`, `Integrations`, `Audit`). It is not the place for narrative taglines ("Is anything on fire?", "Everything that happened, every LEA in scope") or live counts. Operators orient themselves by matching the page heading to the nav item they clicked; the H1 is that pointer, nothing more.

```tsx
// Good: H1 is the nav label, full stop
<h1>LEAs</h1>

// Bad: narrative tagline
<h1>Is anything on fire?</h1>

// Bad: count baked into the H1
<h1>{allRows.length} LEAs</h1>
```

No eyebrow above the H1 by default. The `.eyebrow` class still exists in `design-system.css` for the case where a page genuinely needs a small mono uppercase context line above the H1, but it is opt-in per page and only added when there's specific value to surface; a generic restatement of the nav label ("Local Education Agencies", "Operator dashboard") is not value.

Live counts live below the header in the chip group (per-bucket counts on filter chips) or in the table footer (`{N} loaded` for cursor-paginated tables). They do not live in the H1 because the H1 has to read the same on every page (page-title role) and a count makes it read as data.

## Filter bar order: search input, chip group, dropdowns (in that order)

Every filterable table uses the same filter bar shape. Three slots, always in this order, any of which may be empty:

1. **Search input**, left-aligned (input + leading `&#x2315;` glyph at `fontSize: 20` so the rendered glyph visually matches the placeholder text)
2. **Chip group(s)** — orthogonal status buckets, severity filters, quick-filter chips
3. **Dropdown(s)** — sort selector and/or filter dropdowns that did not earn a chip

Dropdowns always cluster on the **far right**, regardless of whether they sort or filter. When the bar has dropdowns, the first one carries `style={{ marginLeft: "auto" }}` so the dropdown block pushes itself right and the chips stay anchored to the search input (or the left edge when no search is present). Subsequent dropdowns sit immediately after it with the default `gap`.

This is the rule operators rely on without thinking: chips and quick filters live on the left where the eye lands; structured selectors live on the right where the eye expects "settings." Past sessions shipped the audit page with the Action dropdown on the left and the Quick-filters chip in the middle, then had to flip it; do not repeat. CSS classes live in `web/src/design-system.css`.

```tsx
<div className="ds-filterbar">
  {/* 1. Search input, left-aligned */}
  <div style={{ position: "relative", display: "inline-block" }}>
    <span style={{
      position: "absolute",
      left: 8,
      top: "50%",
      transform: "translateY(-50%)",
      color: "var(--ink-4)",
      fontSize: 20,  // glyph doesn't fill its em-square; 20px matches the input's visual weight
    }}>
      &#x2315;
    </span>
    <input
      className="ds-input"
      style={{ width: 320, paddingLeft: 28 }}
      placeholder="Search by LEA name or ID..."
      ...
    />
  </div>

  {/* 2. Chip group(s). One or more; quick-filter chips and bucket chips
       both live in this slot. */}
  <div className="group">
    <span className="lbl">Status</span>
    <div className="ds-chips">
      {BUCKETS.map((bucket) => (
        <button
          className={`ds-chip lvl-${chipSeverity(bucket)} ${active ? "on" : ""}`}
          onClick={() => onToggle(bucket)}
          aria-pressed={active}
        >
          <span className="dot" />
          {LABEL[bucket]} &middot; {counts[bucket]}
        </button>
      ))}
    </div>
  </div>

  {/* 3. Dropdown(s), right-aligned. The first dropdown carries
       marginLeft: auto so the whole cluster pushes right; subsequent
       dropdowns follow at the default gap. */}
  <div className="group" style={{ marginLeft: "auto" }}>
    <span className="lbl">Action</span>
    <select className="ds-select" value={...} onChange={...}>...</select>
  </div>
  <div className="group">
    <span className="lbl">Time range</span>
    <select className="ds-select" value={...} onChange={...}>...</select>
  </div>
</div>
```

Empty slots collapse cleanly:

- No search → chip group sits flush left, dropdowns still right-aligned.
- No chips → search and dropdowns sit at opposite ends.
- No dropdowns → drop `marginLeft: auto` from everything; chips end the bar.

Concrete examples in the codebase:

- `pages/Leas.tsx` — search + severity chips + sort dropdown.
- `pages/Integrations.tsx` — search + status chips + sort dropdown.
- `pages/AdminAudit.tsx` — no search + Quick-filters chip + Action and Time-range dropdowns on the right.

The chip-style filter is the right pattern for orthogonal status buckets with live counts (severity, integration status, sync outcome), and for one-click quick filters that re-write a specific URL state (Audit's "Failed syncs"). Operator sees the breakdown and the live count in one chip and clicks to filter. Dropdown filters (`<select>`) only earn their place when the option list is long, dense, or partner-defined. Past mistakes had four dropdowns on one filter bar (`Partner`, `Status`, `Integration`, `Sharing`); they looked busy without earning the visual weight. The bucket-chip pattern collapses orthogonal categorical filters into one visual unit.

Chip counts are computed against the search + include-revoked scope but ignore the active chip selection so a chip can never read "0" while selected. See `pages/Integrations.tsx` `preBucketRows` / `bucketCounts` for the pattern.

## Sort: dropdown with a small fixed set of named options

Sort is a `<select className="ds-select">` with `value={`${sort}:${dir}`}`. Options are named with the direction arrow:

```
Name ↑                              # default for reference tables
Name ↓
Severity ↓                          # for LEAs
Status (degraded first)             # for Integrations
Cursor lag ↓
Latest sync ↓
```

Default is `name asc` for reference tables (Integrations, future Operators table) and `severity desc` for triage tables (LEAs). The default is encoded in `DEFAULT_SORT` + `DEFAULT_DIR` constants at the top of the page file and matched on both the read path and the URL-clear path.

```tsx
// Good: read path honors DEFAULT_DIR
const dirParam = searchParams.get("dir");
const dir: SortDir =
  dirParam === "asc" || dirParam === "desc" ? dirParam : DEFAULT_DIR;

// Bad: read path hardcodes the fallback
const dir = searchParams.get("dir") === "asc" ? "asc" : "desc";
// ^ silently overrides DEFAULT_DIR when the param is missing
```

This bug shipped to main on the LEAs page and got caught in session 19; do not repeat it.

## No table footer

LEAs and Integrations pages render no table footer. The H1 carries the total, the chips carry the per-bucket counts, the sort dropdown carries the sort state. A footer that says "Showing 5 of 5 · 1 degraded" duplicates information the operator already sees above.

The exception is paginated tables where no total exists (audit explorer). Those get a footer that says "{N} loaded · newest first" plus a Load more / Load older button. Never include a "role: founder_admin · scope: all LEAs" footer line; the header chip on the right already carries the operator's role badge, and "scope: all LEAs" is implied for everyone except the operator role (which gets an empty result set rather than a noisy footer).

## URL-back the filter state

Every filterable table stores its filter, search, and sort state in URL search params via `useSearchParams`. This is non-negotiable. Three reasons:

1. **Refresh-safety.** An operator who refreshes the page does not lose their filter.
2. **Deep links.** Dashboard alerts deep-link into the table with the matching filter pre-applied. Support engineers paste a URL into a ticket and the recipient sees the same view.
3. **Shareable triage state.** During an incident, on-call sends a URL that captures the exact view they were looking at.

Always use `setSearchParams(..., { replace: true })` so back-button does not stack one history entry per keystroke or chip toggle. Use `.delete(key)` when the value matches the default so the URL stays clean on the default view.

```tsx
const updateSort = (nextSort: SortKey, nextDir: SortDir) => {
  setSearchParams(
    (prev) => {
      const next = new URLSearchParams(prev);
      if (nextSort === DEFAULT_SORT && nextDir === DEFAULT_DIR) {
        next.delete("sort");
        next.delete("dir");
      } else {
        next.set("sort", nextSort);
        next.set("dir", nextDir);
      }
      return next;
    },
    { replace: true },
  );
};
```

## Search input search icon

The search icon is a `<span>` rendering the `&#x2315;` unicode glyph at `fontSize: 20`. The glyph doesn't fill its em-square, so 20px is what makes it read at the same visual weight as the placeholder text inside the `.ds-input` (12.5px). Smaller sizes (12.5, 14) all looked like stray punctuation. Color is `var(--ink-4)`. The input's `paddingLeft: 28` accommodates the icon with 8px left offset.

## Multi-tenancy + defensive filter

When a table renders rows fetched with a `lea_id` query param (the LEA drawer's IntegrationSection, future per-LEA drill-downs), add a defensive client-side filter:

```tsx
// Defensive client-side filter. The backend already filters via the
// lea_id query param, but a stale dev server (running an older route
// before the lea_id param landed) would return all rows. This guard
// keeps the drawer from ever rendering another LEA's data.
const rows = (data ?? []).filter((r) => r.lea_id === leaId);
```

The defensive filter caught a real wrong-tenant bug during session B's Integrations refactor when the dev backend had not been restarted to pick up the new route signature. Adding the filter is one line, and the wrong-tenant failure mode is the worst class of bug in a multi-tenant app.

## Combined-status cells

When a table column represents two enums that overlap in steady state (our-side `status` + partner-side `integration_status` on Integrations), do not render them as two adjacent badges. Collapse to one cell that surfaces the partner-side note only on divergence. Use the `combinedStatusView(status, integrationStatus)` helper in `lib/labels.ts`.

```tsx
// Good: one cell, divergence is the signal
<CombinedStatusCell row={row} />

// Bad: two columns of "Active" / "Active" / "Active" / "Active" ...
<td><Badge>{labelForConnectorStatus(row.status)}</Badge></td>
<td><Badge>{labelForIntegrationStatus(row.integration_status)}</Badge></td>
```

The single cell renders the dominant signal as a badge (our-side connector status, since it is what the operator acts on) and the partner-side state as a small note below the badge, colored red when degraded and gray when our-side is terminal (revoked/locked). The two-column shape made steady state look redundant and divergence look weak; the combined cell makes divergence the loudest thing in the row.

## Action menu (per-row dropdown)

Per-row action menus use Chakra v3 `Menu` rendered through a `Portal`. Menu.Content gets explicit styling because Chakra v3 ships with bare-bones defaults; the design system does not currently theme them:

```tsx
<Menu.Content
  style={{
    minWidth: "180px",
    background: "var(--panel)",        // NOT --bg-1; that variable does not exist
    border: "1px solid var(--rule-strong)",
    borderRadius: 8,
    padding: 4,
    boxShadow: "0 8px 28px rgba(0, 0, 0, 0.18)",
    zIndex: 1000,
    fontSize: 13,
  }}
>
  <Menu.Item value="..." onClick={...} style={menuItemStyle()}>
    ...
  </Menu.Item>
</Menu.Content>
```

The `menuItemStyle()` helper lives next to the page file and accepts an optional color override for destructive items (Revoke uses `var(--bad-ink)`). A see-through Menu.Content shipped to main once during session B because `--bg-1` was referenced but never defined; the design system variable for the panel surface is `--panel`.

## Sign-off

Before merging a new or modified table:

- [ ] H1 count is honest (total, not filtered subset; uses `allRows.length` not `visibleRows.length`)
- [ ] Filter bar uses `ds-filterbar` shape and slot order: search input, chip group(s), dropdown(s). Dropdowns cluster on the far right; the first dropdown carries `marginLeft: auto`.
- [ ] Chip filters carry live counts that update with search/include-revoked scope
- [ ] Sort dropdown defaults match `DEFAULT_SORT` / `DEFAULT_DIR` and the read path honors both
- [ ] Filter, search, and sort round-trip through URL params
- [ ] No redundant footer (H1 + chips carry the signal); paginated tables get a "{N} loaded" footer instead
- [ ] If the table is per-LEA-scoped, defensive `.filter((r) => r.lea_id === leaId)` is in place
- [ ] Search input search icon is `fontSize: 20` (cap-height visually matches the 12.5px input text) and uses `var(--ink-4)`
- [ ] Action menus use `var(--panel)` for background and `var(--rule-strong)` for border

## Cross-references

- `web/src/pages/Leas.tsx` + `web/src/components/LeaTable.tsx` — canonical severity-bucketed master list
- `web/src/pages/Integrations.tsx` — canonical cross-LEA roll-up; mirrors LeaTable shape
- `web/src/pages/AdminAudit.tsx` — paginated table without a total (the exception)
- `web/src/lib/leaFilters.ts` — pure helpers; sort + filter + search compose downstream of severity classification
- `web/src/lib/labels.ts` — `combinedStatusView`, `formatPollInterval`, `labelFor*` family
- `.claude/rules/frontend-components.md` — broader component patterns
- `.claude/rules/frontend-copy.md` — label translation gate; enum values never render directly
- `.claude/rules/multi-tenancy.md` — `lea_id` on every query, every cache key, every log line
