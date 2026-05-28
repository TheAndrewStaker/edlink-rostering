import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { Link } from "react-router-dom";

import { api, type AlertOut } from "@/api/client";
import { alertDestination } from "@/lib/alertDestinations";

export function AlertsBanner() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["alerts"],
    queryFn: api.listAlerts,
  });
  const leasQuery = useQuery({ queryKey: ["leas"], queryFn: api.listLeas });
  const leaNameById = useMemo(() => {
    const map = new Map<string, string>();
    for (const lea of leasQuery.data ?? []) {
      map.set(lea.id, lea.name);
    }
    return map;
  }, [leasQuery.data]);

  if (isLoading) {
    return (
      <div className="ds-panel">
        <div className="ds-panel-head">
          <div>
            <h3>Active alerts</h3>
            <div className="sub">Loading...</div>
          </div>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="ds-panel">
        <div className="ds-panel-head">
          <div>
            <h3>Active alerts</h3>
            <div className="sub" style={{ color: "var(--bad-ink)" }}>
              Could not load alerts: {(error as Error).message}
            </div>
          </div>
        </div>
      </div>
    );
  }

  if (!data || data.length === 0) {
    return (
      <div className="ds-panel">
        <div className="ds-panel-head">
          <div>
            <h3>Active alerts</h3>
            <div className="sub">
              Cursor lag, quarantine growth, and reconciliation drift are
              within tolerance across all LEAs.
            </div>
          </div>
          <span className="count">0 firing</span>
        </div>
      </div>
    );
  }

  return (
    <div className="ds-panel">
      <div className="ds-panel-head">
        <div>
          <h3>Active alerts</h3>
          <div className="sub">
            Cursor lag, quarantine growth, reconciliation drift.
          </div>
        </div>
        <span className="count">{data.length} firing</span>
      </div>
      <div>
        {data.map((alert) => (
          <AlertRow
            key={alert.dedup_key}
            alert={alert}
            leaNameById={leaNameById}
          />
        ))}
      </div>
    </div>
  );
}

function AlertRow({
  alert,
  leaNameById,
}: {
  alert: AlertOut;
  leaNameById: Map<string, string>;
}) {
  const severity = alert.severity === "critical" ? "crit" : "warn";
  const label = describe(alert, leaNameById);
  const destination = alertDestination(alert);
  return (
    <div className={`alert-row ${severity}`}>
      <div className="rail" />
      <div>
        <div className="alert-title">{label.title}</div>
        <div className="alert-detail">{label.detail}</div>
      </div>
      {destination && (
        <Link to={destination.href} className="alert-action">
          {destination.label} &rarr;
        </Link>
      )}
    </div>
  );
}

function leaDisplayName(
  alert: AlertOut,
  leaNameById: Map<string, string>,
): string {
  if (!alert.lea_id) return "an unknown LEA";
  return leaNameById.get(alert.lea_id) ?? alert.lea_id;
}

function describe(
  alert: AlertOut,
  leaNameById: Map<string, string>,
): { title: string; detail: string } {
  if (alert.code === "alert.cursor_lag_20_day") {
    const days = alert.measurements["days_behind"];
    const daysText = days != null ? days.toFixed(1) : "?";
    const lea = leaDisplayName(alert, leaNameById);
    return {
      title: `Cursor lag exceeds 20 days for ${lea}`,
      detail: `Last event arrived ${daysText} days ago. EdLink retention is 30 days; cold-start risk grows past day 30.`,
    };
  }
  if (alert.code === "alert.quarantine_growth") {
    const count = alert.measurements["unresolved_count"];
    const lea = leaDisplayName(alert, leaNameById);
    return {
      title: `Quarantine backlog growing for ${lea}`,
      detail: `${count ?? "?"} unresolved orphans in the last 24 hours.`,
    };
  }
  if (alert.code === "alert.reconciliation_drift") {
    const lea = leaDisplayName(alert, leaNameById);
    const types = alert.properties["entity_types"] ?? "";
    const canonicalOnly =
      alert.measurements["canonical_only_count"] ?? 0;
    const partnerOnly = alert.measurements["partner_only_count"] ?? 0;
    const typesText =
      types.length > 0 ? types.split(",").join(", ") : "entities";
    return {
      title: `Reconciliation drift detected for ${lea}`,
      detail: `Merkle diverged on ${typesText}: ${canonicalOnly} canonical-only, ${partnerOnly} partner-only.`,
    };
  }
  return {
    title: alert.code,
    detail: alert.dedup_key,
  };
}
