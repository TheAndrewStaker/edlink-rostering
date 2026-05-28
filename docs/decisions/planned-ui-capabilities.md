# Planned UI capabilities (removed from POC, pending design)

Placeholder buttons and filter controls that were in the admin UI for
design illustration purposes. Removed 2026-05-25 to keep the running
app functional-only. Each capability is documented here for future
implementation.

---

## 1. Run reconciliation sweep (Dashboard)

**Location:** Dashboard page header, primary action button.

**Intended behavior:** Trigger a cross-LEA or per-LEA reconciliation
run from the admin UI. The reconciliation compares canonical data
against the partner's current state (via Merkle hash comparison) and
surfaces drift.

**Design decisions:**
- Button triggers a confirmation dialog asking which LEA(s) to
  reconcile (all vs. selected).
- Runs asynchronously; result appears in the reconciliation panel
  once complete.
- Role gate: `admin` or higher (not `auditor`).

**Backend status:** `POST /leas/{lea_id}/reconciliation` endpoint does
not exist yet. The read side (`GET /leas/{lea_id}/reconciliation`)
exists and surfaces historical runs.

---

## 2. Export CSV (LEAs page)

**Location:** LEAs page header, secondary action button.

**Intended behavior:** Download the current LEA list (with summary
stats: student count, enrollment count, sync status, cursor lag) as a
CSV file for offline reporting or stakeholder distribution.

**Design decisions:**
- Exports whatever the current filter/sort state shows (not always
  all LEAs).
- Columns: LEA name, state, type, student count, enrollment count,
  latest sync status, cursor lag days.
- Generated client-side from the already-fetched TanStack Query cache
  (no dedicated backend endpoint needed).
- Filename pattern: `edlink-leas-YYYY-MM-DD.csv`.

---

## 3. Onboard LEA (LEAs page)

**Location:** LEAs page header, primary action button labeled
"+ Onboard LEA".

**Intended behavior:** Open a guided dialog to register a new LEA in
the system. Onboarding creates the LEA row, sets up the connector
authorization, and optionally triggers a cold-start sync.

**Design decisions:**
- Multi-step dialog: (1) LEA identity (name, state, NCES ID, type),
  (2) connector config (partner, secret ref, poll interval),
  (3) confirmation.
- Creates LEA + connector_authorization rows in one transaction.
- Optionally triggers initial sync on completion.
- Role gate: `admin` or `owner`.

**Backend status:** No `POST /leas` endpoint exists. LEAs are
currently created by the sync worker on first ingest or via seed data.

---

## 4. Import from CLI (Connectors page)

**Location:** Connectors page header, secondary action button.

**Intended behavior:** Import connector authorization records that
were created via the operator CLI (`edlink_rostering.cli`) into the
admin UI's view. This covers the case where an operator authorized a
connector from the terminal and wants to confirm it appears in the
dashboard.

**Design decisions:**
- Opens a dialog explaining the CLI command syntax
  (`python -m edlink_rostering.cli connector authorize ...`).
- Includes a "Refresh" action that invalidates the connectors query
  cache to pick up CLI-created rows.
- May evolve into a paste-JSON-config flow for bulk onboarding.

---

## 5. Authorize connector (Connectors page)

**Location:** Connectors page header, primary action button labeled
"+ Authorize connector".

**Intended behavior:** Open a dialog to create a new connector
authorization for an LEA/partner pair. This is the UI equivalent of
the CLI `connector authorize` command.

**Design decisions:**
- Dialog fields: LEA (select from existing), partner (select),
  secret reference (Key Vault path), poll interval, notes.
- Calls `POST /connectors/{lea_id}/{partner}/authorize` (already
  exists and is functional).
- On success, invalidates connectors query and shows confirmation.
- Role gate: `admin` or `owner`.

**Backend status:** Endpoint exists and works. The `authorizeConnectorDialog`
is already defined in `ConnectorActions.tsx` but was not wired to a
header button (only available via row-level context menu on existing
connectors for re-authorization).

---

## 6. Connector filter bar (Connectors page)

**Location:** Connectors page, filter strip above the table.

**Intended behavior:** Client-side filtering of the connectors table
by LEA name/ID, partner, and status.

**Design decisions:**
- Text input for LEA name/ID: substring match against `lea_name` and
  `lea_id` fields.
- Partner dropdown: filter to rows matching selected partner.
- Status dropdown: filter to rows matching selected status (active,
  pending, locked, revoked).
- All filters are AND-composed and apply client-side against the
  cached connector list.
- Include a hint label: "kv://... = Azure Key Vault secret reference"
  (explains the secret_ref column format).

---

## 7. Export CSV (Audit explorer)

**Location:** Admin Audit page header, secondary action button.

**Intended behavior:** Download the currently-visible audit entries as
a CSV for compliance reporting or incident investigation handoff.

**Design decisions:**
- Exports the current page of entries (respecting active filters).
- Columns: timestamp, actor, action, LEA, target, reason, source.
- Generated client-side from cached query data.
- Filename pattern: `edlink-audit-YYYY-MM-DD.csv`.

---

## 8. Audit explorer: Actor and LEA filters

**Location:** Admin Audit page filter bar, alongside the functional
Action and Time range filters.

**Intended behavior:**
- **Actor filter:** Filter audit entries by actor kind (system vs
  operator) or specific operator email.
- **LEA filter:** Filter audit entries to a specific LEA by name or
  ID substring.

**Design decisions:**
- Actor filter is a select with "All actors", "System", "Operator"
  options. Future: searchable dropdown of known operator emails.
- LEA filter is a text input that filters client-side by substring
  match against the entry's lea_id (resolved to LEA name via the
  cached LEA list).
- Both compose with the existing Action and Time range filters via
  AND logic.

**Backend status:** The `GET /admin/audit` endpoint already accepts
`operator_id` as a query parameter. LEA filtering would require
adding a `lea_id` param to the endpoint (entries carry lea_id in
their detail payload but it's not a top-level filter today).
