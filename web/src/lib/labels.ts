/**
 * Display labels for backend enum values.
 *
 * The API returns lowercase / snake_case values that match the
 * database (e.g., severity "critical", partner "edlink", sync status
 * "quarantine_release"). Operators want proper-cased labels in the
 * UI. Centralizing the mapping here means the API and the UI evolve
 * independently and the brand spelling for each partner ("EdLink",
 * "Ednition") lives in one place.
 *
 * Use the `labelFor*` helpers from components; don't inline the
 * lookup. Components stay focused on rendering, not formatting.
 */

import type { SeverityLevel } from "@/lib/severity";

export const SEVERITY_DISPLAY: Record<SeverityLevel, string> = {
  critical: "Critical",
  warning: "Warning",
  stale: "Stale",
  healthy: "Healthy",
};

export const PARTNER_DISPLAY: Record<string, string> = {
  edlink: "EdLink",
  ednition: "Ednition",
  clever: "Clever",
  oneroster: "OneRoster",
  edfi: "Ed-Fi",
  operator: "Operator",
};

export const SYNC_STATUS_DISPLAY: Record<string, string> = {
  success: "Success",
  failed: "Failed",
  running: "Running",
  revert: "Reverted",
  quarantine_release: "Quarantine release",
};

export const LEA_TYPE_DISPLAY: Record<string, string> = {
  traditional_district: "Traditional district",
  charter_lea: "Charter LEA",
  charter_cmo: "Charter CMO",
  boces: "BOCES",
  state_agency: "State agency",
};

export const CONNECTOR_STATUS_DISPLAY: Record<string, string> = {
  pending: "Pending",
  active: "Active",
  revoked: "Revoked",
  locked: "Locked",
};

/**
 * Display label for EdLink's per-integration status enum.
 *
 * Mirrors the enum from
 * ``docs/partners/edlink-references.md`` § "Integration status". The
 * sync worker polls this every drain and surfaces it on the
 * Integrations page so partner-side disables are visible without
 * cross-referencing EdLink's portal.
 */
export const INTEGRATION_STATUS_DISPLAY: Record<string, string> = {
  inactive: "Inactive",
  active: "Active",
  requested: "Requested",
  disabled: "Disabled",
  destroyed: "Destroyed",
};

export function labelForIntegrationStatus(status: string): string {
  return INTEGRATION_STATUS_DISPLAY[status] ?? capitalize(status);
}

/**
 * Display label for EdLink's per-integration sharing scope.
 *
 * Values are partner-defined; ``full`` is the steady state. Narrower
 * scopes (``read_only``, ``rostering_only``) indicate the district
 * has limited what the integration can do, which is useful context
 * when a sync looks healthy but data is missing.
 */
export const SHARING_SCOPE_DISPLAY: Record<string, string> = {
  full: "Full",
  rostering_only: "Rostering only",
  read_only: "Read-only",
  revoked: "Revoked",
};

export function labelForSharingScope(scope: string): string {
  return SHARING_SCOPE_DISPLAY[scope] ?? capitalize(scope);
}


const KNOWN_INTEGRATION_STATUSES = new Set([
  "inactive",
  "active",
  "requested",
  "disabled",
  "destroyed",
]);

const DEGRADED_INTEGRATION_STATUSES = new Set([
  "inactive",
  "disabled",
  "destroyed",
]);


export type CombinedStatusTone =
  | "ok"
  | "stale"
  | "info"
  | "bad"
  | "mute";


export interface CombinedStatusNote {
  label: string;
  tone: CombinedStatusTone;
}


export interface CombinedStatusView {
  /** Primary badge: dominant signal the operator should react to. */
  primary: { label: string; tone: CombinedStatusTone };
  /** Optional secondary note when our-side and partner-side disagree. */
  partnerNote: CombinedStatusNote | null;
}


/**
 * Derive a combined display state from the our-side authorization
 * ``status`` and the partner-side ``integration_status``.
 *
 * Why combine: the two columns answer different questions but
 * overlap in steady state ("active" / "active"). Rendering them
 * side-by-side as two badges looks redundant when they agree, and
 * loud when they disagree. This helper folds the two values into
 * one primary badge plus an optional partner-side annotation that
 * only renders when the two sides diverge.
 *
 * Rules:
 * - both active → single "Active" badge, no annotation
 * - our-side ``revoked``/``locked`` → the terminal-ish our-side
 *   state is the dominant signal; partner becomes a muted footnote
 *   if it disagrees, hidden when both are at rest
 * - our-side ``active`` + partner disabled/inactive/destroyed →
 *   "Active" badge with a red partner annotation ("EdLink:
 *   Disabled") so on-call sees the partner-side pause loudly
 * - our-side ``pending`` + partner ``requested`` → "Pending" badge
 *   with an info-toned annotation ("EdLink: Requested") so
 *   onboarding state reads as a coherent in-flight signal
 * - unknown / empty partner status → suppress the annotation; the
 *   row hasn't been observed yet, no need to render a footnote
 */
export function combinedStatusView(
  status: string,
  integrationStatus: string | null | undefined,
): CombinedStatusView {
  const partnerKnown =
    integrationStatus != null && KNOWN_INTEGRATION_STATUSES.has(integrationStatus);
  const partnerDegraded =
    partnerKnown && DEGRADED_INTEGRATION_STATUSES.has(integrationStatus!);
  const partnerLabel = partnerKnown
    ? `EdLink: ${labelForIntegrationStatus(integrationStatus!)}`
    : null;

  // Steady state: both sides report active. Single green badge.
  if (status === "active" && integrationStatus === "active") {
    return { primary: { label: "Active", tone: "ok" }, partnerNote: null };
  }

  // Our-side revoked or locked: this is the dominant signal.
  // Partner annotation surfaces only if the partner side disagrees
  // and is in a degraded state worth noting (otherwise it would just
  // restate the obvious).
  if (status === "revoked" || status === "locked") {
    const note: CombinedStatusNote | null =
      partnerKnown && integrationStatus !== "active"
        ? { label: partnerLabel!, tone: "mute" }
        : null;
    return {
      primary: {
        label: CONNECTOR_STATUS_DISPLAY[status] ?? capitalize(status),
        tone: status === "revoked" ? "bad" : "mute",
      },
      partnerNote: note,
    };
  }

  // Pending onboarding: surface partner-side ``requested`` as
  // confirmation of the in-flight handshake, anything else as info.
  if (status === "pending") {
    return {
      primary: { label: "Pending", tone: "stale" },
      partnerNote:
        partnerKnown && integrationStatus !== "active"
          ? { label: partnerLabel!, tone: "info" }
          : null,
    };
  }

  // Our-side active with partner-side disagreement: the most
  // important divergence. Active badge stays green so the operator
  // sees "our row is still authorized" but the red partner note
  // says "EdLink stopped us." Red on the annotation reads as the
  // attention-needing signal.
  if (status === "active") {
    if (partnerDegraded) {
      return {
        primary: { label: "Active", tone: "ok" },
        partnerNote: { label: partnerLabel!, tone: "bad" },
      };
    }
    if (integrationStatus === "requested") {
      return {
        primary: { label: "Active", tone: "ok" },
        partnerNote: { label: partnerLabel!, tone: "info" },
      };
    }
    return { primary: { label: "Active", tone: "ok" }, partnerNote: null };
  }

  // Fallback: unrecognized our-side status. Render whatever the
  // backend returned through the existing label map so the row
  // does not crash on a future enum value.
  return {
    primary: {
      label: CONNECTOR_STATUS_DISPLAY[status] ?? capitalize(status),
      tone: "mute",
    },
    partnerNote:
      partnerKnown && integrationStatus !== "active"
        ? { label: partnerLabel!, tone: "mute" }
        : null,
  };
}

/**
 * Display label for operator roles.
 *
 * Backend enums after V0011: ``owner`` / ``admin`` / ``operator`` /
 * ``auditor``. ``owner`` is the grant-management top tier;
 * ``admin`` is the day-to-day platform admin; ``operator`` is the
 * district-scoped read role; ``auditor`` is the org-wide read-only
 * compliance role. The header badge, the persona switcher menu,
 * and any inline prose render through this helper rather than the
 * raw enum.
 */
export const ROLE_DISPLAY: Record<string, string> = {
  owner: "Owner",
  admin: "Admin",
  operator: "Operator",
  auditor: "Auditor",
};

export function labelForRole(role: string): string {
  return ROLE_DISPLAY[role] ?? sentenceCase(role);
}

export function labelForConnectorStatus(status: string): string {
  return CONNECTOR_STATUS_DISPLAY[status] ?? capitalize(status);
}

export const RECONCILIATION_STATUS_DISPLAY: Record<string, string> = {
  matched: "Matched",
  drift_detected: "Drift detected",
  skipped_quiet_window: "Skipped (quiet window)",
  failed: "Failed",
};

export function labelForReconciliationStatus(status: string): string {
  return RECONCILIATION_STATUS_DISPLAY[status] ?? capitalize(status);
}

/**
 * Display label for the per-LEA timeline `source` column.
 *
 * Each value identifies which underlying table contributed the row;
 * the badge in the timeline section uses this label and the matching
 * color so an operator can scan and recognize which kind of event
 * each timeline row is.
 */
export const TIMELINE_SOURCE_DISPLAY: Record<string, string> = {
  audit_log: "Admin action",
  sync_jobs: "Sync",
  revert_actions: "Revert",
  retry_actions: "Retry",
  quarantine_created: "Quarantine open",
  quarantine_resolved: "Quarantine resolved",
  reconciliation_runs: "Reconciliation",
};

export function labelForTimelineSource(source: string): string {
  return TIMELINE_SOURCE_DISPLAY[source] ?? capitalize(source);
}

/**
 * Display palette per timeline source.
 *
 * Reuses the same color a section already uses elsewhere in the
 * drawer when possible (purple for revert, blue for retry, orange
 * for quarantine, green for reconciliation matched, etc.) so the
 * timeline visually rhymes with the other sections.
 */
export const TIMELINE_SOURCE_COLOR: Record<string, string> = {
  audit_log: "teal",
  sync_jobs: "gray",
  revert_actions: "purple",
  retry_actions: "blue",
  quarantine_created: "orange",
  quarantine_resolved: "green",
  reconciliation_runs: "cyan",
};

export function colorForTimelineSource(source: string): string {
  return TIMELINE_SOURCE_COLOR[source] ?? "gray";
}

/**
 * Display label for the per-LEA timeline `action` column.
 *
 * Actions are stable dotted codes like `connector.authorized`,
 * `sync.failed`, `reconciliation.drift_detected`. Operators want
 * plain English on the row and the raw code in a tooltip.
 */
export const TIMELINE_ACTION_DISPLAY: Record<string, string> = {
  "sync.success": "Sync succeeded",
  "sync.failed": "Sync failed",
  "sync.running": "Sync running",
  "sync.revert": "Operator reverted sync",
  "sync.quarantine_release": "Quarantine release sync",
  "sync.retry_requested": "Operator requested retry",
  "quarantine.created": "Quarantine row opened",
  "quarantine.released": "Quarantine row released",
  "quarantine.rejected": "Quarantine row rejected",
  "quarantine.resolved": "Quarantine row resolved",
  "reconciliation.matched": "Reconciliation matched",
  "reconciliation.drift_detected": "Reconciliation drift detected",
  "reconciliation.skipped_quiet_window":
    "Reconciliation skipped (quiet window)",
  "reconciliation.failed": "Reconciliation failed",
  "connector.authorized": "Connector authorized",
  "connector.revoked": "Connector revoked",
  "connector.credential_rotated": "Connector credential rotated",
  "connector.poll_interval_adjusted": "Connector poll interval adjusted",
};

export function labelForTimelineAction(action: string): string {
  return TIMELINE_ACTION_DISPLAY[action] ?? sentenceCase(action);
}

export function labelForSeverity(severity: SeverityLevel | string): string {
  return (
    SEVERITY_DISPLAY[severity as SeverityLevel] ?? capitalize(severity)
  );
}

export function labelForPartner(partner: string): string {
  return PARTNER_DISPLAY[partner] ?? capitalize(partner);
}

export function labelForSyncStatus(status: string): string {
  return SYNC_STATUS_DISPLAY[status] ?? capitalize(status);
}

export function labelForLeaType(leaType: string): string {
  return LEA_TYPE_DISPLAY[leaType] ?? leaType;
}

function capitalize(value: string): string {
  if (!value) return value;
  return value.charAt(0).toUpperCase() + value.slice(1);
}

/**
 * Map a validation error code to a one-line human label.
 *
 * The backend writes stable codes like `SCHEMA_MISSING_FIELD` and
 * `HTTP_INTEGRITY_FAILED` so log queries are reliable; the operator
 * UI translates those into plain language. Unknown codes get a
 * sentence-cased rendering as a defensive fallback so a new code from
 * the backend never shows as a raw enum.
 */
export const ERROR_CODE_DISPLAY: Record<string, string> = {
  HTTP_INTEGRITY_FAILED: "Partner response failed integrity check",
  SCHEMA_MISSING_FIELD: "Required field missing",
  SCHEMA_INVALID_GRADE: "Grade value not recognized",
  PARSE_INVALID_DATE: "Date could not be parsed",
  PARSE_DATE_ORDER: "End date precedes begin date",
  ENROLLMENT_ORPHAN_STUDENT: "Enrollment references unknown student",
  THRESHOLD_PAGE_OBSERVATION: "Layer 5 observation",
  THRESHOLD_BASELINE_INSUFFICIENT: "Not enough history yet",
  THRESHOLD_EVENT_VOLUME_SPIKE: "Event volume spike vs baseline",
  THRESHOLD_DELETION_BURST: "Deletion burst vs baseline",
  THRESHOLD_POPULATION_SHIFT: "Population shift exceeds threshold",
};

export function labelForErrorCode(code: string): string {
  return ERROR_CODE_DISPLAY[code] ?? sentenceCase(code);
}

/**
 * Parse a sync_jobs.error_summary string like
 * "L2:SCHEMA_MISSING_FIELD@evt_met_010" into a human-readable summary.
 *
 * Format: `L<layer>:<CODE>[@<event_id>][; ...]`. Falls back to the
 * raw string when the shape is unfamiliar.
 */
export function summarizeErrorSummary(raw: string): string {
  const parts = raw.split(";").map((s) => s.trim()).filter(Boolean);
  if (parts.length === 0) return raw;
  return parts
    .map((p) => {
      const match = p.match(/^L(\d+):([A-Z0-9_]+)(?:@(.+))?$/);
      if (!match) return p;
      const [, layer, code, eventId] = match;
      const human = labelForErrorCode(code);
      if (eventId) return `${human} (Layer ${layer}, event ${eventId})`;
      return `${human} (Layer ${layer})`;
    })
    .join("; ");
}

/**
 * Format a poll-interval in seconds as human prose.
 *
 * Used on the connectors page: "300s" is the API contract; "5 min"
 * is what an operator scans. Falls back to seconds for sub-minute
 * intervals so a low-cardinality dev poll like 30s stays readable.
 */
/**
 * Render an integer second count as a human-friendly poll interval.
 *
 * The valid backend range is 60s..3600s (1 minute to 1 hour) per the
 * ``_POLL_INTERVAL_MIN/MAX`` bounds in
 * ``edlink_rostering.services.connector_authz``. Whole hours render as
 * ``"1 hour"`` / ``"2 hours"``; everything else renders as minutes
 * (``"5 min"``, ``"44 min"``).
 *
 * Why minutes everywhere instead of seconds: operators reason about
 * partner refresh cadence in minutes ("every five minutes"), and the
 * slider step is one minute. Seconds stay on the wire because the API
 * contract is ``poll_interval_seconds``; the conversion happens at the
 * UI boundary.
 *
 * Why no days unit: the backend caps the interval at one hour. A
 * future cap above one day would extend this helper, but for the POC
 * minutes and hours cover the whole range.
 */
export function formatPollInterval(seconds: number): string {
  if (seconds <= 0) return "0";
  if (seconds < 60) return `${seconds}s`;
  if (seconds % 3600 === 0) {
    const hours = seconds / 3600;
    return hours === 1 ? "1 hour" : `${hours} hours`;
  }
  const minutes = Math.round(seconds / 60);
  return `${minutes} min`;
}


/**
 * Capitalize a canonical entity type ("enrollment" → "Enrollment").
 *
 * The audit and timeline rows render entity_type/entity_id pairs
 * where the type comes from the canonical schema. Capitalizing the
 * first letter is enough; the underlying value is already
 * descriptive English.
 */
export const ENTITY_TYPE_DISPLAY: Record<string, string> = {
  enrollment: "Enrollment",
  enrollments: "Enrollments",
  student: "Student",
  students: "Students",
  class: "Class",
  classes: "Classes",
  course: "Course",
  school: "School",
  schools: "Schools",
  lea: "LEA",
  academic_session: "Academic session",
  academic_sessions: "Academic sessions",
};


export function labelForEntityType(value: string): string {
  return ENTITY_TYPE_DISPLAY[value] ?? capitalize(value);
}


function sentenceCase(value: string): string {
  if (!value) return value;
  const lower = value.toLowerCase().replace(/_/g, " ");
  return lower.charAt(0).toUpperCase() + lower.slice(1);
}
