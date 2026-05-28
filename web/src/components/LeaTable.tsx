import { useQuery } from "@tanstack/react-query";

import { api, type CursorStateRow } from "@/api/client";
import { DsBadge } from "@/components/DsBadge";
import {
  applySearch,
  applySeverityFilter,
  applySort,
  type SortDir,
  type SortKey,
} from "@/lib/leaFilters";
import {
  labelForLeaType,
  labelForPartner,
  labelForSeverity,
  labelForSyncStatus,
} from "@/lib/labels";
import {
  type ClassifiedLea,
  type SeverityLevel,
  classify,
  quarantineCounts,
} from "@/lib/severity";
import {
  railClassForSeverity,
  toneForSyncStatus,
} from "@/lib/tones";

interface Props {
  selectedLea: string | null;
  onSelect: (leaId: string) => void;
  searchQuery: string;
  onSearchChange: (value: string) => void;
  severityLevels: SeverityLevel[];
  onSeverityToggle: (level: SeverityLevel) => void;
  sort: SortKey;
  dir: SortDir;
  onSortChange: (sort: SortKey, dir: SortDir) => void;
}

const SEVERITY_CHIPS: SeverityLevel[] = [
  "critical",
  "warning",
  "stale",
  "healthy",
];

export function LeaTable({
  selectedLea,
  onSelect,
  searchQuery,
  onSearchChange,
  severityLevels,
  onSeverityToggle,
  sort,
  dir,
  onSortChange,
}: Props) {
  const leas = useQuery({
    queryKey: ["leas"],
    queryFn: api.listLeas,
  });
  const quarantine = useQuery({
    queryKey: ["quarantine"],
    queryFn: () => api.listQuarantine(),
  });
  const cursors = useQuery({
    queryKey: ["cursors"],
    queryFn: () => api.listCursors(),
  });

  const cursorByLea: Record<string, CursorStateRow | undefined> = {};
  for (const c of cursors.data ?? []) {
    cursorByLea[c.lea_id] = c;
  }
  const qCounts = quarantineCounts(quarantine.data ?? []);
  const classified: ClassifiedLea[] = (leas.data ?? []).map((lea) =>
    classify(lea, qCounts[lea.id] ?? 0),
  );

  const severityCounts: Record<SeverityLevel, number> = {
    critical: 0,
    warning: 0,
    stale: 0,
    healthy: 0,
  };
  for (const row of classified) {
    severityCounts[row.severity]++;
  }

  const afterSearch = applySearch(classified, searchQuery);
  const afterFilter = applySeverityFilter(afterSearch, severityLevels);
  const rows = applySort(afterFilter, sort, dir, cursorByLea);
  const totalLeas = leas.data?.length ?? 0;

  return (
    <div className="ds-panel">
      {/* Filter bar */}
      <div className="ds-filterbar">
        <div style={{ position: "relative", display: "inline-block" }}>
          <span
            style={{
              position: "absolute",
              left: 8,
              top: "50%",
              transform: "translateY(-50%)",
              color: "var(--ink-4)",
              fontSize: 20,
            }}
          >
            &#x2315;
          </span>
          <input
            className="ds-input"
            style={{ width: 320, paddingLeft: 28 }}
            placeholder="Search by LEA name, ID, state..."
            value={searchQuery}
            onChange={(e) => onSearchChange(e.target.value)}
            aria-label="Search LEAs"
          />
        </div>
        <div className="group">
          <span className="lbl">Severity</span>
          <div className="ds-chips">
            {SEVERITY_CHIPS.map((level) => {
              const active = severityLevels.includes(level);
              return (
                <button
                  key={level}
                  type="button"
                  className={`ds-chip lvl-${level} ${active ? "on" : ""}`}
                  onClick={() => onSeverityToggle(level)}
                  aria-pressed={active}
                >
                  <span className="dot" />
                  {labelForSeverity(level)} &middot; {severityCounts[level]}
                </button>
              );
            })}
          </div>
        </div>
        <div className="group" style={{ marginLeft: "auto" }}>
          <span className="lbl">Sort</span>
          <select
            className="ds-select"
            value={`${sort}:${dir}`}
            onChange={(e) => {
              const [k, d] = e.target.value.split(":") as [SortKey, SortDir];
              onSortChange(k, d);
            }}
          >
            <option value="severity:desc">Severity &darr;</option>
            <option value="name:asc">Name &uarr;</option>
            <option value="cursor_lag_days:desc">Cursor lag &darr;</option>
            <option value="latest_sync_at:desc">Latest sync &darr;</option>
          </select>
        </div>
      </div>

      {/* Loading / error / empty states */}
      {leas.isLoading && (
        <div style={{ padding: 24 }}>
          <div style={{ height: 36, background: "var(--bg-2)", borderRadius: 4, marginBottom: 8 }} />
          <div style={{ height: 36, background: "var(--bg-2)", borderRadius: 4, marginBottom: 8 }} />
          <div style={{ height: 36, background: "var(--bg-2)", borderRadius: 4 }} />
        </div>
      )}

      {leas.error && (
        <div style={{ padding: 24, fontSize: 13, color: "var(--bad-ink)" }}>
          Could not load LEAs: {(leas.error as Error).message}
        </div>
      )}

      {leas.data && leas.data.length === 0 && (
        <div style={{ padding: 24, fontSize: 13, color: "var(--ink-3)" }}>
          No LEAs onboarded yet. Run{" "}
          <code className="mono">bash scripts/seed-dev.sh</code> to populate
          the demo set.
        </div>
      )}

      {/* Table */}
      {rows.length > 0 && (
        <table className="ds-tbl">
          <thead>
            <tr>
              <th>LEA</th>
              <th style={{ width: 80 }}>Type</th>
              <th style={{ width: 100 }}>Partner</th>
              <th style={{ width: 110 }}>Sync status</th>
              <th className="num" style={{ width: 90 }}>Students</th>
              <th className="num" style={{ width: 110 }}>Enrollments</th>
              <th className="num" style={{ width: 100 }}>Cursor lag</th>
              <th style={{ width: 110 }}>Latest sync</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <LeaRow
                key={row.lea.id}
                row={row}
                cursor={cursorByLea[row.lea.id]}
                selected={row.lea.id === selectedLea}
                onClick={() => onSelect(row.lea.id)}
              />
            ))}
          </tbody>
        </table>
      )}

      {/* Footer */}
      {leas.data && leas.data.length > 0 && (
        <div className="ds-tbl-foot">
          <span>
            Showing {rows.length} of {totalLeas}
            {severityLevels.length > 0 &&
              ` · ${totalLeas - rows.length} filtered out by severity`}
          </span>
          <span className="mono">auto-refresh 10s</span>
        </div>
      )}
    </div>
  );
}

interface LeaRowProps {
  row: ClassifiedLea;
  cursor: CursorStateRow | undefined;
  selected: boolean;
  onClick: () => void;
}

function LeaRow({ row, cursor, selected, onClick }: LeaRowProps) {
  const { lea, severity } = row;
  const lagWarn = lea.cursor_lag_days != null && lea.cursor_lag_days > 20;
  const lagStale = lea.cursor_lag_days != null && lea.cursor_lag_days > 0.5;

  return (
    <tr
      onClick={onClick}
      style={{ cursor: "pointer" }}
      className={selected ? "selected" : ""}
      title={row.reasons.join(" · ")}
    >
      <td>
        <div style={{ display: "flex", alignItems: "center" }}>
          <span className={`tbl-rail ${railClassForSeverity(severity)}`} />
          <div className="cell-stack">
            <span className="name">{lea.name}</span>
            <span className="id">
              {lea.id} &middot; {lea.state}
            </span>
          </div>
        </div>
      </td>
      <td>
        <DsBadge tone="ghost">{labelForLeaType(lea.lea_type)}</DsBadge>
      </td>
      <td>
        {cursor ? (
          <span
            className="mono"
            style={{ fontSize: 11.5, color: "var(--ink-2)" }}
          >
            {labelForPartner(cursor.partner)}
          </span>
        ) : (
          <span style={{ fontSize: 12, color: "var(--ink-3)" }}>
            Not configured
          </span>
        )}
      </td>
      <td>
        {lea.latest_sync_status ? (
          <DsBadge tone={toneForSyncStatus(lea.latest_sync_status)}>
            {labelForSyncStatus(lea.latest_sync_status)}
          </DsBadge>
        ) : (
          <span style={{ fontSize: 12, color: "var(--ink-3)" }}>
            Never synced
          </span>
        )}
      </td>
      <td className="num">{lea.student_count.toLocaleString()}</td>
      <td className="num">{lea.enrollment_count.toLocaleString()}</td>
      <td
        className="num mono"
        style={{
          color: lagWarn
            ? "var(--bad-ink)"
            : lagStale
              ? "var(--stale-ink)"
              : "var(--ink-2)",
        }}
      >
        {lea.cursor_lag_days == null
          ? "—"
          : `${lea.cursor_lag_days.toFixed(1)}d`}
      </td>
      <td
        className="mono"
        style={{ fontSize: 11.5, color: "var(--ink-2)" }}
      >
        {lea.latest_sync_at ? relativeTime(lea.latest_sync_at) : "—"}
      </td>
    </tr>
  );
}

function relativeTime(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  const minutes = ms / 60_000;
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${Math.round(minutes)}m ago`;
  const hours = minutes / 60;
  if (hours < 24) return `${Math.round(hours)}h ago`;
  const days = hours / 24;
  return `${Math.round(days)}d ago`;
}
