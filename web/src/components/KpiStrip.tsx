import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { api } from "@/api/client";

export function KpiStrip() {
  const leas = useQuery({ queryKey: ["leas"], queryFn: api.listLeas });
  const alerts = useQuery({ queryKey: ["alerts"], queryFn: api.listAlerts });
  const quarantine = useQuery({
    queryKey: ["quarantine"],
    queryFn: () => api.listQuarantine(),
  });

  const totalLeas = leas.data?.length ?? 0;
  const failed = (leas.data ?? []).filter(
    (l) => l.latest_sync_status === "failed",
  ).length;
  const stale = (leas.data ?? []).filter(
    (l) => l.cursor_lag_days != null && l.cursor_lag_days > 20,
  ).length;
  const healthy = Math.max(0, totalLeas - failed - stale);
  const needsAttention = totalLeas - healthy;
  const activeAlerts = alerts.data?.length ?? 0;
  const quarantineBacklog = (quarantine.data ?? []).filter(
    (r) => r.resolved_at == null,
  ).length;
  const maxLag = (leas.data ?? []).reduce(
    (max, l) =>
      l.cursor_lag_days != null && l.cursor_lag_days > max
        ? l.cursor_lag_days
        : max,
    0,
  );
  const inFlight = (leas.data ?? []).reduce(
    (sum, l) => sum + (l.in_flight_count ?? 0),
    0,
  );

  return (
    <div className="kpi-grid">
      <KpiTile
        label="LEAs healthy"
        value={
          <>
            {healthy}
            <span style={{ color: "var(--ink-3)", fontSize: 18 }}>
              /{totalLeas || 0}
            </span>
          </>
        }
        helpText={
          totalLeas === 0 ? (
            "no LEAs onboarded"
          ) : healthy === totalLeas ? (
            "all clear"
          ) : (
            <Link
              to="/leas?severity=critical&severity=warning"
              className="kpi-help-link"
            >
              {needsAttention} need attention &rarr;
            </Link>
          )
        }
        tone={
          totalLeas === 0
            ? "neutral"
            : healthy === totalLeas
              ? "good"
              : "bad"
        }
      />
      <KpiTile
        label="Active alerts"
        value={activeAlerts}
        helpText={
          activeAlerts === 0
            ? "no alerts firing"
            : `${activeAlerts} firing right now`
        }
        tone={activeAlerts === 0 ? "good" : "bad"}
      />
      <KpiTile
        label="Quarantine backlog"
        value={quarantineBacklog}
        helpText={
          quarantineBacklog === 0
            ? "queue empty"
            : "unresolved orphan rows"
        }
        tone={
          quarantineBacklog === 0
            ? "good"
            : quarantineBacklog > 25
              ? "bad"
              : "warn"
        }
      />
      <KpiTile
        label="Max cursor lag"
        value={
          <>
            {maxLag.toFixed(1)}
            <span style={{ fontSize: 18, color: "var(--ink-3)" }}>d</span>
          </>
        }
        helpText={
          maxLag === 0
            ? "all fresh"
            : maxLag > 20
              ? "past 20-day alert threshold"
              : "within tolerance"
        }
        tone={maxLag > 20 ? "bad" : maxLag > 1 ? "warn" : "good"}
      />
      <KpiTile
        label="In flight"
        value={inFlight}
        helpText={
          inFlight === 0
            ? "no syncs running"
            : `${inFlight} sync${inFlight === 1 ? "" : "s"} in progress`
        }
        tone={inFlight === 0 ? "neutral" : "warn"}
      />
    </div>
  );
}

type Tone = "good" | "warn" | "bad" | "neutral";

interface KpiTileProps {
  label: string;
  value: React.ReactNode;
  helpText: React.ReactNode;
  tone: Tone;
}

function KpiTile({ label, value, helpText, tone }: KpiTileProps) {
  return (
    <div className={`kpi ${tone}`}>
      <span className="rail" />
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">{value}</div>
      <div className="kpi-help">{helpText}</div>
    </div>
  );
}
