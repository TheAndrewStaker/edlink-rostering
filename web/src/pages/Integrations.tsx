/**
 * Integrations page.
 *
 * Cross-LEA view of connector authorization rows. One row per
 * ``(lea, partner)`` pair, with the connector lifecycle actions
 * (Authorize / Revoke / Rotate credential / Adjust poll interval)
 * scoped to a single integration.
 *
 * Why "Integrations" and not "Connectors":
 *
 * - The schema table is ``connector_authorization`` and the backend
 *   route stays ``/api/v1/connectors`` for backwards compatibility
 *   with operator CLI clients; the user-facing surface is renamed
 *   because operators reason about "integrations" (a moving part on
 *   the partner side), not "connectors" (the framework primitive).
 * - A single LEA can have N integrations (partial unique index on
 *   ``(lea_id, partner) WHERE revoked_at IS NULL`` allows one live
 *   row per partner per LEA). Today every LEA has exactly one
 *   EdLink row; the next partner (Ednition / Clever / Ed-Fi) ships
 *   without a UI change.
 *
 * Filters compose via URL search params so support engineers can
 * paste a deep link into a ticket and see the same filtered view:
 *
 *   /integrations?lea=lea-hillcrest-usd
 *   /integrations?integration_status=disabled
 *   /integrations?include_revoked=true&partner=edlink
 *
 * The LEA detail drawer's "Manage" link in
 * :func:`IntegrationSection` deep-links here with the matching
 * ``lea`` filter pre-applied.
 */

import { Menu, Portal } from "@chakra-ui/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo } from "react";
import { useSearchParams } from "react-router-dom";

import type { ConnectorAuthorizationOut } from "@/api/client";
import { api } from "@/api/client";
import {
  adjustPollIntervalDialog,
  authorizeConnectorDialog,
  revokeConnectorDialog,
  rotateCredentialDialog,
} from "@/components/ConnectorActions";
import { DsBadge } from "@/components/DsBadge";
import {
  combinedStatusView,
  formatPollInterval,
  labelForPartner,
  labelForSharingScope,
} from "@/lib/labels";
import { notifyError, notifySuccess } from "@/lib/notify";
import { toneForPartner } from "@/lib/tones";

/**
 * Bucketed status filter mirrors the LEAs page's severity-chip shape:
 * a small set of orthogonal labels operators recognize at a glance.
 *
 * - ``healthy``: our-side active AND partner-side active. Steady state.
 * - ``degraded``: partner-side ``inactive``/``disabled``/``destroyed``.
 *   The whole reason the integration_status poll exists.
 * - ``pending``: our-side ``pending``/``locked`` or partner-side
 *   ``requested``. Onboarding or compliance hold.
 * - ``revoked``: our-side ``revoked``. Only visible when the
 *   include-revoked toggle is on.
 *
 * Buckets compose with the include-revoked checkbox. Selecting
 * ``revoked`` without ticking include-revoked produces an empty
 * result, which is the honest answer ("no revoked rows are visible
 * because revoked rows are hidden by default").
 */
type StatusBucket = "healthy" | "degraded" | "pending" | "revoked";

const STATUS_BUCKETS: readonly StatusBucket[] = [
  "healthy",
  "degraded",
  "pending",
  "revoked",
];

const STATUS_BUCKET_LABEL: Record<StatusBucket, string> = {
  healthy: "Healthy",
  degraded: "Degraded",
  pending: "Pending",
  revoked: "Revoked",
};

type FilterState = {
  lea: string;
  statuses: StatusBucket[];
  include_revoked: boolean;
};

type SortKey = "name" | "status" | "partner" | "poll_interval";
type SortDir = "asc" | "desc";

const DEFAULT_SORT: SortKey = "name";
const DEFAULT_DIR: SortDir = "asc";
const SORT_KEYS: readonly SortKey[] = [
  "name",
  "status",
  "partner",
  "poll_interval",
];

function readFilters(params: URLSearchParams): FilterState {
  return {
    lea: params.get("lea") ?? "",
    statuses: params
      .getAll("status")
      .filter((v): v is StatusBucket =>
        STATUS_BUCKETS.includes(v as StatusBucket),
      ),
    include_revoked: params.get("include_revoked") === "true",
  };
}

function statusBucketFor(row: ConnectorAuthorizationOut): StatusBucket {
  if (row.status === "revoked") return "revoked";
  if (row.status === "pending" || row.status === "locked") return "pending";
  if (
    row.integration_status === "inactive" ||
    row.integration_status === "disabled" ||
    row.integration_status === "destroyed"
  )
    return "degraded";
  if (row.integration_status === "requested") return "pending";
  return "healthy";
}

function readSort(params: URLSearchParams): {
  sort: SortKey;
  dir: SortDir;
} {
  const rawSort = params.get("sort");
  const sort: SortKey = SORT_KEYS.includes(rawSort as SortKey)
    ? (rawSort as SortKey)
    : DEFAULT_SORT;
  const rawDir = params.get("dir");
  const dir: SortDir =
    rawDir === "asc" || rawDir === "desc" ? rawDir : DEFAULT_DIR;
  return { sort, dir };
}

/**
 * "Urgency" rank for the combined status cell so a degraded-first
 * sort surfaces the rows that need attention. Higher rank = more
 * urgent. The values are not exposed to the operator; only the
 * relative ordering matters.
 */
function urgencyRank(row: ConnectorAuthorizationOut): number {
  // Partner-side degraded (our side still says active) is the top of
  // the triage list. The whole reason we added the integration_status
  // poll is to catch this divergence.
  const partnerDegraded =
    row.integration_status === "inactive" ||
    row.integration_status === "disabled" ||
    row.integration_status === "destroyed";
  if (row.status === "active" && partnerDegraded) return 100;
  if (row.status === "locked") return 80;
  if (row.status === "pending") return 60;
  if (row.status === "active") return 40;
  if (row.status === "revoked") return 20;
  return 0;
}

function applyIntegrationSort(
  rows: ConnectorAuthorizationOut[],
  sort: SortKey,
  dir: SortDir,
): ConnectorAuthorizationOut[] {
  const sign = dir === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    const primary = compareIntegrations(a, b, sort);
    if (primary !== 0) return primary * sign;
    return a.lea_name.localeCompare(b.lea_name);
  });
}

function compareIntegrations(
  a: ConnectorAuthorizationOut,
  b: ConnectorAuthorizationOut,
  sort: SortKey,
): number {
  switch (sort) {
    case "name":
      return a.lea_name.localeCompare(b.lea_name);
    case "status":
      return urgencyRank(a) - urgencyRank(b);
    case "partner":
      return a.partner.localeCompare(b.partner);
    case "poll_interval":
      return a.poll_interval_seconds - b.poll_interval_seconds;
  }
}

export function IntegrationsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = readFilters(searchParams);
  const { sort, dir } = readSort(searchParams);
  const queryClient = useQueryClient();

  const QUERY_KEY = useMemo(
    () => ["connectors", filters.lea, filters.include_revoked] as const,
    [filters.lea, filters.include_revoked],
  );

  const { data, isLoading, isError } = useQuery({
    queryKey: QUERY_KEY,
    queryFn: () =>
      api.listConnectors({
        lea_id: filters.lea || undefined,
        include_revoked: filters.include_revoked || undefined,
      }),
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["connectors"] });

  const updateLea = (value: string) => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (!value) next.delete("lea");
        else next.set("lea", value);
        return next;
      },
      { replace: true },
    );
  };

  const toggleStatus = (bucket: StatusBucket) => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        const current = prev.getAll("status");
        next.delete("status");
        if (current.includes(bucket)) {
          for (const v of current) if (v !== bucket) next.append("status", v);
        } else {
          for (const v of current) next.append("status", v);
          next.append("status", bucket);
        }
        return next;
      },
      { replace: true },
    );
  };

  const toggleIncludeRevoked = (value: boolean) => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (value) next.set("include_revoked", "true");
        else next.delete("include_revoked");
        return next;
      },
      { replace: true },
    );
  };

  const updateSort = (nextSort: SortKey, nextDir: SortDir) => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (nextSort === DEFAULT_SORT && nextDir === DEFAULT_DIR) {
          next.delete("sort");
          next.delete("dir");
        } else {
          next.set("sort", nextSort);
          next.set("dir", nextDir);
        }
        return next;
      },
      { replace: true },
    );
  };

  const authorizeMutation = useMutation({
    mutationFn: ({
      lea_id,
      partner,
      secret_ref,
      reason,
      poll_interval_seconds,
    }: {
      lea_id: string;
      partner: string;
      secret_ref: string;
      reason: string;
      poll_interval_seconds?: number;
    }) =>
      api.authorizeConnector(lea_id, partner, {
        secret_ref,
        reason,
        poll_interval_seconds,
      }),
    onSuccess: (result) => {
      notifySuccess(
        `Authorized ${result.lea_id} on ${result.partner}.`,
      );
    },
    onError: (err: Error) => {
      notifyError(`Authorize failed: ${err.message}`);
    },
    onSettled: invalidate,
  });

  const revokeMutation = useMutation({
    mutationFn: ({
      lea_id,
      partner,
      reason,
    }: {
      lea_id: string;
      partner: string;
      reason: string;
    }) => api.revokeConnector(lea_id, partner, reason),
    onSuccess: (result) => {
      notifySuccess(`Revoked ${result.lea_id} on ${result.partner}.`);
    },
    onError: (err: Error) => {
      notifyError(`Revoke failed: ${err.message}`);
    },
    onSettled: invalidate,
  });

  const rotateMutation = useMutation({
    mutationFn: ({
      lea_id,
      partner,
      new_secret_ref,
      reason,
    }: {
      lea_id: string;
      partner: string;
      new_secret_ref: string;
      reason: string;
    }) =>
      api.rotateConnectorCredential(lea_id, partner, new_secret_ref, reason),
    onSuccess: (result) => {
      notifySuccess(
        `Rotated credential for ${result.lea_id} on ${result.partner}.`,
      );
    },
    onError: (err: Error) => {
      notifyError(`Rotate failed: ${err.message}`);
    },
    onSettled: invalidate,
  });

  const adjustMutation = useMutation({
    mutationFn: ({
      lea_id,
      partner,
      new_poll_interval_seconds,
      reason,
    }: {
      lea_id: string;
      partner: string;
      new_poll_interval_seconds: number;
      reason: string;
    }) =>
      api.adjustConnectorPollInterval(
        lea_id,
        partner,
        new_poll_interval_seconds,
        reason,
      ),
    onSuccess: (result) => {
      notifySuccess(
        `Poll interval set to ${formatPollInterval(result.new_poll_interval_seconds)} for ${result.lea_id}.`,
      );
    },
    onError: (err: Error) => {
      notifyError(`Poll-interval adjust failed: ${err.message}`);
    },
    onSettled: invalidate,
  });

  const onAuthorize = async (row: ConnectorAuthorizationOut) => {
    const result = await authorizeConnectorDialog.open({ row });
    if (!result) return;
    authorizeMutation.mutate({
      lea_id: row.lea_id,
      partner: row.partner,
      ...result,
    });
  };

  const onRevoke = async (row: ConnectorAuthorizationOut) => {
    const result = await revokeConnectorDialog.open({
      row,
      config: {
        title: `Revoke ${row.lea_name}'s ${labelForPartner(row.partner)} integration`,
        description:
          "Marks the row revoked and blocks subsequent sync polls. The audit row carries the reason. Re-authorize is a separate action that inserts a new row.",
        defaultReason: "",
        confirmLabel: "Revoke",
        confirmPalette: "red",
      },
    });
    if (!result) return;
    revokeMutation.mutate({
      lea_id: row.lea_id,
      partner: row.partner,
      reason: result.reason,
    });
  };

  const onRotate = async (row: ConnectorAuthorizationOut) => {
    const result = await rotateCredentialDialog.open({ row });
    if (!result) return;
    rotateMutation.mutate({
      lea_id: row.lea_id,
      partner: row.partner,
      ...result,
    });
  };

  const onAdjust = async (row: ConnectorAuthorizationOut) => {
    const result = await adjustPollIntervalDialog.open({ row });
    if (!result) return;
    adjustMutation.mutate({
      lea_id: row.lea_id,
      partner: row.partner,
      ...result,
    });
  };

  const allRows = data ?? [];
  // Buckets count against the search + include-revoked scope but ignore
  // the active bucket selection so a chip can never read "0" while
  // selected. Matches the LEAs page's severity-count behavior.
  const preBucketRows = applyClientFilters(allRows, {
    ...filters,
    statuses: [],
  });
  const bucketCounts: Record<StatusBucket, number> = {
    healthy: 0,
    degraded: 0,
    pending: 0,
    revoked: 0,
  };
  for (const r of preBucketRows) bucketCounts[statusBucketFor(r)]++;
  const filteredRows = applyClientFilters(allRows, filters);
  const visibleRows = applyIntegrationSort(filteredRows, sort, dir);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Integrations</h1>
        </div>
        <div className="actions" />
      </div>

      <div className="ds-panel">
        <FilterBar
          filters={filters}
          bucketCounts={bucketCounts}
          onLeaChange={updateLea}
          onStatusToggle={toggleStatus}
          onIncludeRevokedChange={toggleIncludeRevoked}
          sort={sort}
          dir={dir}
          onSortChange={updateSort}
        />

        {isLoading && (
          <div style={{ padding: 24 }}>
            <div
              style={{
                height: 48,
                background: "var(--bg-2)",
                borderRadius: 4,
                marginBottom: 8,
              }}
            />
            <div
              style={{
                height: 48,
                background: "var(--bg-2)",
                borderRadius: 4,
              }}
            />
          </div>
        )}

        {isError && (
          <div style={{ padding: 24, fontSize: 13, color: "var(--bad-ink)" }}>
            Could not load integrations. The admin API may be restarting; the
            dashboard will retry automatically.
          </div>
        )}

        {data && data.length === 0 && (
          <div style={{ padding: 24, fontSize: 13, color: "var(--ink-3)" }}>
            No integrations yet. Authorize an LEA from the admin surface
            or the operator CLI to populate this page.
          </div>
        )}

        {data && data.length > 0 && visibleRows.length === 0 && (
          <div style={{ padding: 24, fontSize: 13, color: "var(--ink-3)" }}>
            No integrations match the current filters. Clear a filter to widen
            the result set.
          </div>
        )}

        {visibleRows.length > 0 && (
          <table className="ds-tbl">
            <thead>
              <tr>
                <th>LEA</th>
                <th style={{ width: 90 }}>Partner</th>
                <th style={{ width: 130 }}>Status</th>
                <th style={{ width: 100 }}>Sharing</th>
                <th style={{ width: 150 }}>Authorized by</th>
                <th className="num" style={{ width: 90 }}>
                  Poll interval
                </th>
                <th style={{ width: 170 }}>Secret ref</th>
                <th style={{ width: 90, textAlign: "right" }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {visibleRows.map((row) => (
                <tr key={row.id}>
                  <td>
                    <div className="cell-stack">
                      <span className="name">{row.lea_name}</span>
                      <span className="id">{row.lea_id}</span>
                    </div>
                  </td>
                  <td>
                    <DsBadge tone={toneForPartner(row.partner)}>
                      {labelForPartner(row.partner)}
                    </DsBadge>
                  </td>
                  <td>
                    <CombinedStatusCell row={row} />
                  </td>
                  <td
                    style={{
                      color: "var(--ink-2)",
                      fontSize: 12,
                    }}
                  >
                    {row.sharing_scope
                      ? labelForSharingScope(row.sharing_scope)
                      : "—"}
                  </td>
                  <td
                    style={{
                      color: "var(--ink-2)",
                      fontSize: 12,
                    }}
                  >
                    {row.authorized_by_email ?? "—"}
                  </td>
                  <td
                    className="num mono"
                    style={{
                      color: "var(--ink-2)",
                      fontSize: 12,
                    }}
                    title={`${row.poll_interval_seconds} seconds on the wire`}
                  >
                    {formatPollInterval(row.poll_interval_seconds)}
                  </td>
                  <td
                    className="mono"
                    style={{
                      fontSize: 11,
                      color: "var(--ink-3)",
                    }}
                    title={row.secret_ref}
                  >
                    {compactSecret(row.secret_ref)}
                  </td>
                  <td style={{ textAlign: "right" }}>
                    <Menu.Root>
                      <Menu.Trigger asChild>
                        <button
                          className="ds-btn small"
                          type="button"
                          style={{ whiteSpace: "nowrap" }}
                          disabled={row.status === "revoked"}
                          title={
                            row.status === "revoked"
                              ? "Revoked integrations cannot be modified. Re-authorize via the admin surface or the CLI."
                              : undefined
                          }
                        >
                          Actions ▾
                        </button>
                      </Menu.Trigger>
                      <Portal>
                        <Menu.Positioner>
                          <Menu.Content
                            style={{
                              minWidth: "180px",
                              background: "var(--panel)",
                              border: "1px solid var(--rule-strong)",
                              borderRadius: 8,
                              padding: 4,
                              boxShadow:
                                "0 8px 28px rgba(0, 0, 0, 0.18)",
                              zIndex: 1000,
                              fontSize: 13,
                            }}
                          >
                            <Menu.Item
                              value="rotate"
                              onClick={() => onRotate(row)}
                              style={menuItemStyle()}
                            >
                              Rotate credential
                            </Menu.Item>
                            <Menu.Item
                              value="adjust"
                              onClick={() => onAdjust(row)}
                              style={menuItemStyle()}
                            >
                              Adjust poll interval
                            </Menu.Item>
                            <Menu.Item
                              value="authorize"
                              onClick={() => onAuthorize(row)}
                              style={menuItemStyle()}
                            >
                              Re-authorize
                            </Menu.Item>
                            <Menu.Item
                              value="revoke"
                              onClick={() => onRevoke(row)}
                              style={menuItemStyle("var(--bad-ink)")}
                            >
                              Revoke
                            </Menu.Item>
                          </Menu.Content>
                        </Menu.Positioner>
                      </Portal>
                    </Menu.Root>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

      </div>
    </>
  );
}

/**
 * Filter bar mirrors the LEAs page's shape so the two surfaces feel
 * like the same admin surface:
 *
 * - one search input on the left
 * - one chip group in the middle for the status bucket (Healthy /
 *   Degraded / Pending / Revoked) with live counts
 * - one sort dropdown on the right
 *
 * Partner / Integration-status / Sharing-scope dropdowns are
 * intentionally NOT here. They added visual weight without earning
 * it at five rows of EdLink-only data; we can bring back targeted
 * filters when the data set widens (Ednition / Clever / Ed-Fi
 * rollout) or when an operator surfaces a real workflow that the
 * bucket chips cannot answer.
 */
function FilterBar({
  filters,
  bucketCounts,
  onLeaChange,
  onStatusToggle,
  onIncludeRevokedChange,
  sort,
  dir,
  onSortChange,
}: {
  filters: FilterState;
  bucketCounts: Record<StatusBucket, number>;
  onLeaChange: (value: string) => void;
  onStatusToggle: (bucket: StatusBucket) => void;
  onIncludeRevokedChange: (value: boolean) => void;
  sort: SortKey;
  dir: SortDir;
  onSortChange: (sort: SortKey, dir: SortDir) => void;
}) {
  return (
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
          placeholder="Search by LEA name or ID..."
          value={filters.lea}
          onChange={(e) => onLeaChange(e.target.value)}
          aria-label="Search integrations"
        />
      </div>
      <div className="group">
        <span className="lbl">Status</span>
        <div className="ds-chips">
          {STATUS_BUCKETS.map((bucket) => {
            const active = filters.statuses.includes(bucket);
            return (
              <button
                key={bucket}
                type="button"
                className={`ds-chip lvl-${chipSeverity(bucket)} ${active ? "on" : ""}`}
                onClick={() => onStatusToggle(bucket)}
                aria-pressed={active}
              >
                <span className="dot" />
                {STATUS_BUCKET_LABEL[bucket]} &middot; {bucketCounts[bucket]}
              </button>
            );
          })}
        </div>
      </div>
      <label
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
          fontSize: 13,
          color: "var(--ink-2)",
          whiteSpace: "nowrap",
        }}
      >
        <input
          type="checkbox"
          checked={filters.include_revoked}
          onChange={(e) => onIncludeRevokedChange(e.target.checked)}
        />
        Include revoked
      </label>
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
          <option value="name:asc">Name &uarr;</option>
          <option value="name:desc">Name &darr;</option>
          <option value="status:desc">Status (degraded first)</option>
          <option value="poll_interval:asc">
            Poll interval (tightest)
          </option>
          <option value="poll_interval:desc">
            Poll interval (loosest)
          </option>
        </select>
      </div>
    </div>
  );
}

/**
 * Map a status bucket to the severity-chip CSS class so the chips
 * pick up the same dot color the LEAs page uses ("lvl-critical" /
 * "lvl-warning" / "lvl-stale" / "lvl-healthy"). Reusing the class
 * names means the two pages stay visually identical without a CSS
 * change.
 */
function chipSeverity(bucket: StatusBucket): string {
  switch (bucket) {
    case "degraded":
      return "critical";
    case "pending":
      return "warning";
    case "revoked":
      return "stale";
    case "healthy":
      return "healthy";
  }
}

function applyClientFilters(
  rows: ConnectorAuthorizationOut[],
  filters: FilterState,
): ConnectorAuthorizationOut[] {
  if (filters.statuses.length === 0) return rows;
  const set = new Set(filters.statuses);
  return rows.filter((r) => set.has(statusBucketFor(r)));
}

/**
 * Combined our-side / partner-side status cell.
 *
 * Primary badge is the our-side authorization status; the partner-side
 * ``integration_status`` becomes a small annotation below when the
 * two sides disagree, and is suppressed entirely when they agree or
 * when the partner side has not been observed yet. See
 * :func:`combinedStatusView` for the full divergence matrix.
 */
function CombinedStatusCell({ row }: { row: ConnectorAuthorizationOut }) {
  const view = combinedStatusView(row.status, row.integration_status);
  const observedTitle = row.integration_status_observed_at
    ? `EdLink last observed ${new Date(
        row.integration_status_observed_at,
      ).toLocaleString()}`
    : "EdLink integration not yet observed";
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "flex-start",
        gap: 3,
      }}
    >
      <DsBadge tone={view.primary.tone}>{view.primary.label}</DsBadge>
      {view.partnerNote && (
        <span
          style={{
            fontSize: 11,
            color:
              view.partnerNote.tone === "bad"
                ? "var(--bad-ink)"
                : view.partnerNote.tone === "info"
                  ? "var(--info-ink, #1565c0)"
                  : "var(--ink-3)",
          }}
          title={observedTitle}
        >
          {view.partnerNote.label}
        </span>
      )}
    </div>
  );
}


function compactSecret(value: string): string {
  if (value.length <= 28) return value;
  return `${value.slice(0, 16)}…${value.slice(-8)}`;
}


/**
 * Inline style for action-menu items so they read consistently
 * across the page. Matches the DevPersonaSwitcher menu pattern.
 */
function menuItemStyle(color?: string): React.CSSProperties {
  return {
    padding: "8px 12px",
    borderRadius: 6,
    cursor: "pointer",
    fontSize: 13,
    color: color ?? "var(--ink)",
  };
}
