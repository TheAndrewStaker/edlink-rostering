import type { BadgeTone } from "@/components/DsBadge";
import type { SeverityLevel } from "@/lib/severity";

export function toneForSeverity(severity: SeverityLevel): BadgeTone {
  switch (severity) {
    case "critical":
      return "bad";
    case "warning":
      return "warn";
    case "stale":
      return "stale";
    case "healthy":
      return "ok";
  }
}

export function toneForSyncStatus(
  status: string,
): BadgeTone {
  switch (status) {
    case "success":
      return "ok";
    case "failed":
      return "bad";
    case "running":
      return "info";
    case "revert":
      return "mute";
    case "quarantine_release":
      return "ghost";
    default:
      return "ghost";
  }
}

export function toneForConnectorStatus(
  status: string,
): BadgeTone {
  switch (status) {
    case "active":
      return "ok";
    case "pending":
      return "stale";
    case "locked":
      return "ghost";
    case "revoked":
      return "bad";
    default:
      return "ghost";
  }
}

export function toneForPartner(partner: string): BadgeTone {
  switch (partner) {
    case "edlink":
      return "info";
    default:
      return "mute";
  }
}

/**
 * Badge tone for EdLink's per-integration status enum.
 *
 * ``inactive``/``disabled``/``destroyed`` are the three degraded
 * states that drive the ``integration_degraded`` alert; render them
 * as ``bad`` so the table column reads "stop everything" at a
 * glance. ``requested`` is the partner-side equivalent of pending
 * and renders ``stale`` to match the ``pending`` connector status
 * tone. ``active`` is ``ok``.
 */
export function toneForIntegrationStatus(status: string): BadgeTone {
  switch (status) {
    case "active":
      return "ok";
    case "requested":
      return "stale";
    case "inactive":
    case "disabled":
    case "destroyed":
      return "bad";
    default:
      return "ghost";
  }
}

export function toneForAlertSeverity(severity: string): "bad" | "warn" {
  return severity === "critical" ? "bad" : "warn";
}

export function railClassForSeverity(severity: SeverityLevel): string {
  switch (severity) {
    case "critical":
      return "crit";
    case "warning":
      return "warn";
    case "stale":
      return "stale";
    case "healthy":
      return "ok";
  }
}

export function toneForTimelineSource(source: string): BadgeTone {
  switch (source) {
    case "sync_jobs":
      return "info";
    case "reconciliation_runs":
      return "mute";
    case "quarantine_created":
    case "quarantine_resolved":
      return "warn";
    case "revert_actions":
    case "retry_actions":
      return "mute";
    case "audit_log":
      return "info";
    default:
      return "ghost";
  }
}

export function toneForTimelineAction(action: string): BadgeTone {
  if (action.includes("failed") || action.includes("drift") || action.includes("rejected")) return "bad";
  if (action.includes("warning") || action.includes("quarantine") || action.includes("schema")) return "warn";
  if (action.includes("success") || action.includes("matched") || action.includes("released") || action.includes("committed")) return "ok";
  if (action.includes("revert") || action.includes("retry")) return "mute";
  return "info";
}
