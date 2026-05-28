/**
 * Client-side severity classification for the LEA dashboard.
 *
 * Operators triage by clicking the loudest row first. The table sorts
 * descending by severity rank so the worst state lands at the top and
 * "healthy" sinks to the bottom regardless of LEA name.
 *
 * The rules are intentionally simple and explicit: latest sync failed
 * beats cursor lag over 20 days beats quarantine backlog over 10 rows
 * beats stale cursor over 12 hours, with everything else healthy. The
 * thresholds match the alert evaluators on the backend
 * (`edlink_rostering/services/alerts.py`) so the UI severity reads
 * the same scenario the alert engine would flag.
 */

import type { LeaSummary, QuarantineRowOut } from "@/api/client";

export type SeverityLevel = "critical" | "warning" | "stale" | "healthy";

const RANK: Record<SeverityLevel, number> = {
  critical: 3,
  warning: 2,
  stale: 1,
  healthy: 0,
};

export const SEVERITY_COLOR: Record<SeverityLevel, string> = {
  critical: "red.500",
  warning: "orange.500",
  stale: "yellow.500",
  healthy: "green.500",
};

export const SEVERITY_LABEL: Record<SeverityLevel, string> = {
  critical: "critical",
  warning: "warning",
  stale: "stale",
  healthy: "healthy",
};

export interface ClassifiedLea {
  lea: LeaSummary;
  severity: SeverityLevel;
  reasons: string[];
}

export function classify(
  lea: LeaSummary,
  quarantineCount: number,
): ClassifiedLea {
  const reasons: string[] = [];
  let severity: SeverityLevel = "healthy";

  if (lea.latest_sync_status === "failed") {
    severity = "critical";
    reasons.push("latest sync failed");
  }
  if (lea.cursor_lag_days != null && lea.cursor_lag_days > 20) {
    severity = "critical";
    reasons.push(
      `cursor ${lea.cursor_lag_days.toFixed(1)} days behind`,
    );
  }
  if (quarantineCount > 10 && severity !== "critical") {
    severity = "warning";
    reasons.push(`${quarantineCount} quarantined rows`);
  }
  if (
    lea.cursor_lag_days != null &&
    lea.cursor_lag_days > 0.5 &&
    severity === "healthy"
  ) {
    severity = "stale";
    reasons.push(`cursor ${lea.cursor_lag_days.toFixed(1)}d behind`);
  }
  if (severity === "healthy") {
    reasons.push("healthy");
  }
  return { lea, severity, reasons };
}

export function sortBySeverity(classified: ClassifiedLea[]): ClassifiedLea[] {
  return [...classified].sort((a, b) => {
    const rankDiff = RANK[b.severity] - RANK[a.severity];
    if (rankDiff !== 0) return rankDiff;
    return a.lea.name.localeCompare(b.lea.name);
  });
}

export function quarantineCounts(
  rows: QuarantineRowOut[],
): Record<string, number> {
  const out: Record<string, number> = {};
  for (const row of rows) {
    if (row.resolved_at != null) continue;
    out[row.lea_id] = (out[row.lea_id] ?? 0) + 1;
  }
  return out;
}
