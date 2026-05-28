/**
 * Unit tests for the sort/search/filter helpers that back the /leas
 * page affordances. The helpers are pure so the test fixture builds
 * `ClassifiedLea` rows by hand without going through the queryClient.
 */

import { describe, expect, it } from "vitest";

import type { CursorStateRow, LeaSummary } from "@/api/client";
import {
  applySearch,
  applySeverityFilter,
  applySort,
} from "@/lib/leaFilters";
import type { ClassifiedLea, SeverityLevel } from "@/lib/severity";

function makeLea(overrides: Partial<LeaSummary> & { id: string; name: string }): LeaSummary {
  const base: LeaSummary = {
    id: overrides.id,
    name: overrides.name,
    lea_type: "traditional_district",
    state: "CA",
    student_count: 1000,
    enrollment_count: 4000,
    latest_sync_at: "2026-05-22T10:00:00Z",
    latest_sync_status: "success",
    cursor_lag_days: 1,
    in_flight_count: 0,
  };
  return { ...base, ...overrides };
}

function makeRow(
  id: string,
  name: string,
  severity: SeverityLevel,
  overrides: Partial<LeaSummary> = {},
): ClassifiedLea {
  return {
    lea: makeLea({ id, name, ...overrides }),
    severity,
    reasons: [],
  };
}

function makeCursor(lea_id: string, partner: string): CursorStateRow {
  return {
    lea_id,
    partner,
    last_event_id: null,
    last_event_at: null,
    last_poll_at: null,
    cold_start_required: false,
    days_behind: null,
  };
}

describe("applySort", () => {
  it("sorts by name ascending using localeCompare", () => {
    const rows = [
      makeRow("a", "Lakewood USD", "healthy"),
      makeRow("b", "Andover", "healthy"),
      makeRow("c", "Berkeley USD", "healthy"),
    ];
    const sorted = applySort(rows, "name", "asc");
    expect(sorted.map((r) => r.lea.name)).toEqual([
      "Andover",
      "Berkeley USD",
      "Lakewood USD",
    ]);
  });

  it("sorts by name descending", () => {
    const rows = [
      makeRow("a", "Andover", "healthy"),
      makeRow("b", "Berkeley USD", "healthy"),
      makeRow("c", "Lakewood USD", "healthy"),
    ];
    const sorted = applySort(rows, "name", "desc");
    expect(sorted.map((r) => r.lea.name)).toEqual([
      "Lakewood USD",
      "Berkeley USD",
      "Andover",
    ]);
  });

  it("sorts by severity descending with critical first", () => {
    const rows = [
      makeRow("a", "Alpha", "healthy"),
      makeRow("b", "Bravo", "critical"),
      makeRow("c", "Charlie", "stale"),
      makeRow("d", "Delta", "warning"),
    ];
    const sorted = applySort(rows, "severity", "desc");
    expect(sorted.map((r) => r.severity)).toEqual([
      "critical",
      "warning",
      "stale",
      "healthy",
    ]);
  });

  it("sorts by severity ascending with healthy first", () => {
    const rows = [
      makeRow("a", "Alpha", "critical"),
      makeRow("b", "Bravo", "healthy"),
      makeRow("c", "Charlie", "warning"),
    ];
    const sorted = applySort(rows, "severity", "asc");
    expect(sorted.map((r) => r.severity)).toEqual([
      "healthy",
      "warning",
      "critical",
    ]);
  });

  it("sorts by student_count descending numerically", () => {
    const rows = [
      makeRow("a", "Alpha", "healthy", { student_count: 100 }),
      makeRow("b", "Bravo", "healthy", { student_count: 10_000 }),
      makeRow("c", "Charlie", "healthy", { student_count: 500 }),
    ];
    const sorted = applySort(rows, "student_count", "desc");
    expect(sorted.map((r) => r.lea.student_count)).toEqual([
      10_000,
      500,
      100,
    ]);
  });

  it("sorts by latest_sync_at descending with null last", () => {
    const rows = [
      makeRow("a", "Alpha", "healthy", {
        latest_sync_at: "2026-05-22T10:00:00Z",
      }),
      makeRow("b", "Never", "healthy", { latest_sync_at: null }),
      makeRow("c", "Charlie", "healthy", {
        latest_sync_at: "2026-05-22T18:00:00Z",
      }),
    ];
    const sorted = applySort(rows, "latest_sync_at", "desc");
    expect(sorted.map((r) => r.lea.id)).toEqual(["c", "a", "b"]);
  });

  it("sorts by cursor_lag_days ascending with null last", () => {
    const rows = [
      makeRow("a", "Alpha", "healthy", { cursor_lag_days: 5 }),
      makeRow("b", "NoCursor", "healthy", { cursor_lag_days: null }),
      makeRow("c", "Charlie", "healthy", { cursor_lag_days: 0.3 }),
    ];
    const sorted = applySort(rows, "cursor_lag_days", "asc");
    expect(sorted.map((r) => r.lea.id)).toEqual(["c", "a", "b"]);
  });

  it("sorts by partner with rows missing a cursor sorting last", () => {
    const rows = [
      makeRow("a", "Alpha", "healthy"),
      makeRow("b", "Bravo", "healthy"),
      makeRow("c", "Charlie", "healthy"),
    ];
    const cursors: Record<string, CursorStateRow> = {
      a: makeCursor("a", "ednition"),
      c: makeCursor("c", "edlink"),
    };
    const sorted = applySort(rows, "partner", "asc", cursors);
    expect(sorted.map((r) => r.lea.id)).toEqual(["c", "a", "b"]);
  });
});

describe("applySearch", () => {
  it("matches name case-insensitively against a substring", () => {
    const rows = [
      makeRow("lea-lakewood-usd", "Lakewood USD", "healthy"),
      makeRow("lea-andover", "Andover", "healthy"),
    ];
    const matched = applySearch(rows, "LAKEW");
    expect(matched.map((r) => r.lea.id)).toEqual(["lea-lakewood-usd"]);
  });

  it("matches against the LEA id slug as well as the name", () => {
    const rows = [
      makeRow("lea-valley-charter", "Valley Charter", "healthy"),
      makeRow("lea-lakewood-usd", "Lakewood USD", "healthy"),
    ];
    const matched = applySearch(rows, "valley");
    expect(matched.map((r) => r.lea.id)).toEqual(["lea-valley-charter"]);
  });

  it("returns all rows when the query is empty after trim", () => {
    const rows = [
      makeRow("a", "Alpha", "healthy"),
      makeRow("b", "Bravo", "healthy"),
    ];
    const matched = applySearch(rows, "   ");
    expect(matched).toHaveLength(2);
  });
});

describe("applySeverityFilter", () => {
  it("keeps only rows whose severity is in the selected set", () => {
    const rows = [
      makeRow("a", "Alpha", "critical"),
      makeRow("b", "Bravo", "warning"),
      makeRow("c", "Charlie", "healthy"),
    ];
    const filtered = applySeverityFilter(rows, ["critical", "warning"]);
    expect(filtered.map((r) => r.lea.id)).toEqual(["a", "b"]);
  });

  it("returns all rows when no severity levels are selected (no filter)", () => {
    const rows = [
      makeRow("a", "Alpha", "critical"),
      makeRow("b", "Bravo", "healthy"),
    ];
    const filtered = applySeverityFilter(rows, []);
    expect(filtered).toHaveLength(2);
  });

  it("filters by a single severity level", () => {
    const rows = [
      makeRow("a", "Alpha", "critical"),
      makeRow("b", "Bravo", "warning"),
      makeRow("c", "Charlie", "stale"),
    ];
    const filtered = applySeverityFilter(rows, ["stale"]);
    expect(filtered.map((r) => r.lea.id)).toEqual(["c"]);
  });
});
