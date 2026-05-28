import { describe, expect, it } from "vitest";

import type { AlertOut } from "@/api/client";
import { alertDestination } from "@/lib/alertDestinations";

function makeAlert(overrides: Partial<AlertOut>): AlertOut {
  return {
    code: "alert.sync_failure",
    severity: "warning",
    dedup_key: "x",
    lea_id: "lea-test",
    measurements: {},
    properties: {},
    ...overrides,
  };
}

describe("alertDestination", () => {
  it("routes LEA-drawer alerts to /leas?lea=<id>", () => {
    const codes = [
      "alert.sync_failure",
      "alert.schema_drift",
      "alert.cursor_lag_20_day",
      "alert.reconciliation_drift",
      "alert.quarantine_growth",
    ];
    for (const code of codes) {
      const dest = alertDestination(makeAlert({ code, lea_id: "lea-hill" }));
      expect(dest).toEqual({
        href: "/leas?lea=lea-hill",
        label: "Open LEA",
      });
    }
  });

  it("routes integration_degraded to /integrations?lea=<id>", () => {
    const dest = alertDestination(
      makeAlert({ code: "alert.integration_degraded", lea_id: "lea-hill" }),
    );
    expect(dest).toEqual({
      href: "/integrations?lea=lea-hill",
      label: "Open integration",
    });
  });

  it("returns null for platform-level alerts with no LEA destination", () => {
    expect(
      alertDestination(makeAlert({ code: "alert.retention_policy_drift" })),
    ).toBeNull();
    expect(
      alertDestination(makeAlert({ code: "alert.idempotency_table_growth" })),
    ).toBeNull();
  });

  it("returns null for any alert without a lea_id", () => {
    expect(
      alertDestination(makeAlert({ code: "alert.sync_failure", lea_id: null })),
    ).toBeNull();
  });

  it("returns null for an unknown alert code", () => {
    expect(
      alertDestination(makeAlert({ code: "alert.brand_new_code" })),
    ).toBeNull();
  });

  it("URL-encodes the lea_id", () => {
    const dest = alertDestination(
      makeAlert({ code: "alert.sync_failure", lea_id: "lea/with slash" }),
    );
    expect(dest?.href).toBe("/leas?lea=lea%2Fwith%20slash");
  });
});
