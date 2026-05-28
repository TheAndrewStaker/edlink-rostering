/**
 * Map an active alert to the page where the operator's next action lives.
 *
 * State-derived alerts (cursor lag, quarantine growth, reconciliation
 * drift) and row-creation alerts (sync failure, schema drift) land on
 * the LEAs page with the matching LEA's drawer open. The drawer's
 * Sync timeline, Cursor section, Quarantine queue, and Reconciliation
 * drift section sit in one scroll, so a single `?lea=<id>` deep link
 * is enough; auto-scroll-to-section is deferred.
 *
 * Integration-degraded alerts land on the Integrations page. That is
 * where the operator acts via the row's lifecycle dropdown (Authorize
 * / Rotate / Adjust poll interval). The page parses `?lea=<id>` as a
 * filter and shows the matching row.
 *
 * Platform-level alerts (`alert.retention_policy_drift`,
 * `alert.idempotency_table_growth`) have no per-LEA destination
 * today; this helper returns `null` and the AlertsBanner renders no
 * sub-link for those rows.
 */

import type { AlertOut } from "@/api/client";

export interface AlertDestination {
  href: string;
  label: string;
}

const LEA_DRAWER_ALERTS = new Set([
  "alert.sync_failure",
  "alert.schema_drift",
  "alert.cursor_lag_20_day",
  "alert.reconciliation_drift",
  "alert.quarantine_growth",
]);

export function alertDestination(alert: AlertOut): AlertDestination | null {
  if (alert.code === "alert.integration_degraded" && alert.lea_id) {
    return {
      href: `/integrations?lea=${encodeURIComponent(alert.lea_id)}`,
      label: "Open integration",
    };
  }
  if (LEA_DRAWER_ALERTS.has(alert.code) && alert.lea_id) {
    return {
      href: `/leas?lea=${encodeURIComponent(alert.lea_id)}`,
      label: "Open LEA",
    };
  }
  return null;
}
