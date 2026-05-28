/**
 * Pure helpers for the /leas table affordances: sort, name search, and
 * severity filter. Composes downstream of severity classification.
 *
 * Pipeline: `classify(leas)` -> `applySearch(rows, q)` ->
 * `applySeverityFilter(rows, levels)` -> `applySort(rows, sort, dir)`.
 * Each helper is pure so the URL-state wiring in the page component
 * stays a thin orchestration layer.
 *
 * Sort keys mirror the columns rendered by `LeaTable`. Rows missing
 * the sort key (null partner, null latest_sync_at, null cursor_lag_days)
 * always trail the value-bearing rows regardless of direction so the
 * "missing data" rows are predictable to find.
 */

import type { CursorStateRow } from "@/api/client";
import type { ClassifiedLea, SeverityLevel } from "@/lib/severity";

export type SortKey =
  | "name"
  | "severity"
  | "partner"
  | "student_count"
  | "enrollment_count"
  | "latest_sync_at"
  | "cursor_lag_days";

export type SortDir = "asc" | "desc";

const SEVERITY_RANK: Record<SeverityLevel, number> = {
  critical: 3,
  warning: 2,
  stale: 1,
  healthy: 0,
};

export function applySearch(
  rows: ClassifiedLea[],
  q: string,
): ClassifiedLea[] {
  const needle = q.trim().toLowerCase();
  if (needle === "") return rows;
  return rows.filter(
    (r) =>
      r.lea.name.toLowerCase().includes(needle) ||
      r.lea.id.toLowerCase().includes(needle),
  );
}

export function applySeverityFilter(
  rows: ClassifiedLea[],
  levels: SeverityLevel[],
): ClassifiedLea[] {
  if (levels.length === 0) return rows;
  const set = new Set(levels);
  return rows.filter((r) => set.has(r.severity));
}

export function applySort(
  rows: ClassifiedLea[],
  sort: SortKey,
  dir: SortDir,
  cursorByLea: Record<string, CursorStateRow | undefined> = {},
): ClassifiedLea[] {
  const sign = dir === "asc" ? 1 : -1;
  const withValue: ClassifiedLea[] = [];
  const withoutValue: ClassifiedLea[] = [];
  for (const row of rows) {
    if (hasValue(row, sort, cursorByLea)) withValue.push(row);
    else withoutValue.push(row);
  }
  withValue.sort((a, b) => {
    const primary = compareBy(a, b, sort, cursorByLea);
    if (primary !== 0) return primary * sign;
    return a.lea.name.localeCompare(b.lea.name);
  });
  withoutValue.sort((a, b) => a.lea.name.localeCompare(b.lea.name));
  return [...withValue, ...withoutValue];
}

function hasValue(
  row: ClassifiedLea,
  sort: SortKey,
  cursorByLea: Record<string, CursorStateRow | undefined>,
): boolean {
  switch (sort) {
    case "partner":
      return cursorByLea[row.lea.id]?.partner != null;
    case "latest_sync_at":
      return row.lea.latest_sync_at != null;
    case "cursor_lag_days":
      return row.lea.cursor_lag_days != null;
    default:
      return true;
  }
}

function compareBy(
  a: ClassifiedLea,
  b: ClassifiedLea,
  sort: SortKey,
  cursorByLea: Record<string, CursorStateRow | undefined>,
): number {
  switch (sort) {
    case "name":
      return a.lea.name.localeCompare(b.lea.name);
    case "severity":
      return SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity];
    case "partner": {
      const ap = cursorByLea[a.lea.id]?.partner ?? "";
      const bp = cursorByLea[b.lea.id]?.partner ?? "";
      return ap.localeCompare(bp);
    }
    case "student_count":
      return a.lea.student_count - b.lea.student_count;
    case "enrollment_count":
      return a.lea.enrollment_count - b.lea.enrollment_count;
    case "latest_sync_at": {
      const at = new Date(a.lea.latest_sync_at ?? 0).getTime();
      const bt = new Date(b.lea.latest_sync_at ?? 0).getTime();
      return at - bt;
    }
    case "cursor_lag_days":
      return (a.lea.cursor_lag_days ?? 0) - (b.lea.cursor_lag_days ?? 0);
  }
}
