import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router-dom";

import type {
  AuditExplorerPage,
  AuditExplorerQuery,
  LeaSummary,
  TimelineEntryOut,
} from "@/api/client";
import { api } from "@/api/client";
import { DsBadge } from "@/components/DsBadge";
import {
  labelForEntityType,
  labelForPartner,
  labelForTimelineAction,
  labelForTimelineSource,
  summarizeErrorSummary,
} from "@/lib/labels";
import { toneForTimelineSource } from "@/lib/tones";

const PAGE_SIZE = 25;

interface AuditPageCursor {
  cursor_occurred_at: string;
  cursor_id: string;
}

const ACTION_PREFIX_OPTIONS = [
  { value: "", label: "All actions" },
  { value: "sync.", label: "Sync events" },
  { value: "reconciliation.", label: "Reconciliation" },
  { value: "connector.", label: "Connector lifecycle" },
  { value: "quarantine.", label: "Quarantine events" },
];

const TIME_WINDOW_OPTIONS: {
  value: string;
  label: string;
  hours: number | null;
}[] = [
  { value: "all", label: "All time", hours: null },
  { value: "1h", label: "Last hour", hours: 1 },
  { value: "24h", label: "Last 24 hours", hours: 24 },
  { value: "7d", label: "Last 7 days", hours: 24 * 7 },
];

// The "Failed syncs" quick-filter chip writes a specific action value
// (``sync.failed``) that is narrower than any dropdown bucket. It
// composes with the dropdown visually: when the chip is active, the
// Action dropdown still shows ``sync.`` so the operator sees both
// signals (broader bucket + the chip's narrowing) and the dropdown
// does not lie about the active filter.
const FAILED_SYNCS_ACTION = "sync.failed";
const FAILED_SYNCS_TIME = "24h";

const ACTION_PREFIX_VALUES = new Set([
  ...ACTION_PREFIX_OPTIONS.map((o) => o.value),
  FAILED_SYNCS_ACTION,
]);
const TIME_WINDOW_VALUES = new Set(TIME_WINDOW_OPTIONS.map((o) => o.value));

function dropdownActionValue(actionPrefix: string): string {
  if (actionPrefix === FAILED_SYNCS_ACTION) return "sync.";
  return actionPrefix;
}

export function AdminAuditPage() {
  const [searchParams, setSearchParams] = useSearchParams();

  // URL is the source of truth for the filter selection. Unknown
  // values fall back to the defaults so a stale or hand-edited link
  // does not strand the operator in an unfilterable state.
  const actionPrefixParam = searchParams.get("action") ?? "";
  const actionPrefix = ACTION_PREFIX_VALUES.has(actionPrefixParam)
    ? actionPrefixParam
    : "";
  const timeWindowParam = searchParams.get("time") ?? "all";
  const timeWindow = TIME_WINDOW_VALUES.has(timeWindowParam)
    ? timeWindowParam
    : "all";

  const setActionPrefix = useCallback(
    (value: string) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (value === "") next.delete("action");
          else next.set("action", value);
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  const setTimeWindow = useCallback(
    (value: string) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (value === "all") next.delete("time");
          else next.set("time", value);
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  const failedSyncsActive =
    actionPrefix === FAILED_SYNCS_ACTION && timeWindow === FAILED_SYNCS_TIME;
  const toggleFailedSyncs = useCallback(() => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (failedSyncsActive) {
          next.delete("action");
          next.delete("time");
        } else {
          next.set("action", FAILED_SYNCS_ACTION);
          next.set("time", FAILED_SYNCS_TIME);
        }
        return next;
      },
      { replace: true },
    );
  }, [failedSyncsActive, setSearchParams]);

  const since = useMemo(() => {
    const opt = TIME_WINDOW_OPTIONS.find((o) => o.value === timeWindow);
    if (!opt?.hours) return undefined;
    return new Date(Date.now() - opt.hours * 3600 * 1000).toISOString();
  }, [timeWindow]);

  const leasQuery = useQuery({
    queryKey: ["leas"],
    queryFn: api.listLeas,
  });
  const leaNameById = useMemo(() => {
    const map = new Map<string, string>();
    for (const lea of leasQuery.data ?? []) {
      map.set(lea.id, lea.name);
    }
    return map;
  }, [leasQuery.data]);

  const queryKey = useMemo(
    () => ["audit", actionPrefix, timeWindow] as const,
    [actionPrefix, timeWindow],
  );

  const audit = useInfiniteQuery({
    queryKey,
    initialPageParam: null as AuditPageCursor | null,
    queryFn: async ({ pageParam }) => {
      const query: AuditExplorerQuery = {
        limit: PAGE_SIZE,
        ...(actionPrefix ? { action_prefix: actionPrefix } : {}),
        ...(since ? { since } : {}),
        ...(pageParam ?? {}),
      };
      return api.listAuditEntries(query);
    },
    getNextPageParam: (last: AuditExplorerPage): AuditPageCursor | null =>
      last.next_cursor
        ? {
            cursor_occurred_at: last.next_cursor.occurred_at,
            cursor_id: last.next_cursor.id,
          }
        : null,
  });

  const entries = useMemo(
    () =>
      audit.data?.pages.flatMap((p) => p.entries) ??
      ([] as TimelineEntryOut[]),
    [audit.data],
  );

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Audit</h1>
        </div>
        <div className="actions" />
      </div>

      <div className="ds-panel">
        {/* Filter bar
            Order follows the table convention: search input, chip
            group, dropdowns on the far right. This page has no search
            input today; the chip group leads and the two dropdowns are
            pushed right via marginLeft: auto on the first one. */}
        <div className="ds-filterbar">
          <div className="group">
            <span className="lbl">Quick filters</span>
            <div className="ds-chips">
              <button
                type="button"
                className={`ds-chip lvl-critical ${
                  failedSyncsActive ? "on" : ""
                }`}
                onClick={toggleFailedSyncs}
                aria-pressed={failedSyncsActive}
              >
                <span className="dot" />
                Failed syncs
              </button>
            </div>
          </div>
          <div className="group" style={{ marginLeft: "auto" }}>
            <span className="lbl">Action</span>
            <select
              className="ds-select"
              style={{ minWidth: 200 }}
              value={dropdownActionValue(actionPrefix)}
              onChange={(e) => setActionPrefix(e.target.value)}
            >
              {ACTION_PREFIX_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </div>
          <div className="group">
            <span className="lbl">Time range</span>
            <select
              className="ds-select"
              value={timeWindow}
              onChange={(e) => setTimeWindow(e.target.value)}
            >
              {TIME_WINDOW_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </div>
        </div>

        {/* Loading / error / empty */}
        {audit.isLoading && (
          <div style={{ padding: 24 }}>
            <div
              style={{
                height: 56,
                background: "var(--bg-2)",
                borderRadius: 4,
                marginBottom: 8,
              }}
            />
            <div
              style={{
                height: 56,
                background: "var(--bg-2)",
                borderRadius: 4,
                marginBottom: 8,
              }}
            />
            <div
              style={{
                height: 56,
                background: "var(--bg-2)",
                borderRadius: 4,
              }}
            />
          </div>
        )}

        {audit.isError && (
          <div style={{ padding: 24, fontSize: 13, color: "var(--bad-ink)" }}>
            Could not load the audit explorer. The admin API may be restarting;
            the page will retry on refresh.
          </div>
        )}

        {!audit.isLoading && !audit.isError && entries.length === 0 && (
          <div style={{ padding: 24, fontSize: 13, color: "var(--ink-3)" }}>
            No entries match the current filters. Widen the time range or clear
            the action filter to see more history.
          </div>
        )}

        {/* Table */}
        {entries.length > 0 && (
          <table className="ds-tbl">
            <thead>
              <tr>
                <th style={{ width: 140 }}>When</th>
                <th style={{ width: 200 }}>LEA</th>
                <th style={{ width: 140 }}>Source</th>
                <th>Action and detail</th>
                <th style={{ width: 200 }}>Actor</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry) => (
                <AuditRow
                  key={entry.id}
                  entry={entry}
                  leaNameById={leaNameById}
                />
              ))}
            </tbody>
          </table>
        )}

        {/* Footer
            The audit feed is cursor-paginated and the backend never
            returns a total because UNIONing six sources for COUNT(*)
            is expensive at scale. The footer is honest about that:
            it surfaces the size of the currently-loaded window (which
            grows as Load more fires) plus the "newest first" reminder,
            and ends with a definitive "end of history" message when
            ``hasNextPage`` is false. */}
        {entries.length > 0 && (
          <div className="ds-tbl-foot">
            <span style={{ fontSize: 12, color: "var(--ink-3)" }}>
              {entries.length} loaded &middot; newest first
              {audit.hasNextPage ? null : " · end of history"}
            </span>
            {audit.hasNextPage && (
              <button
                className="ds-btn small"
                type="button"
                onClick={() => audit.fetchNextPage()}
                disabled={audit.isFetchingNextPage}
                title={
                  entries[entries.length - 1]?.occurred_at
                    ? `Next cursor: ${new Date(
                        entries[entries.length - 1].occurred_at,
                      ).toISOString()}`
                    : undefined
                }
              >
                {audit.isFetchingNextPage
                  ? "Loading..."
                  : "Load older →"}
              </button>
            )}
          </div>
        )}
      </div>
    </>
  );
}

function AuditRow({
  entry,
  leaNameById,
}: {
  entry: TimelineEntryOut;
  leaNameById: Map<string, LeaSummary["id"]>;
}) {
  const leaIdFromDetail =
    typeof entry.detail?.lea_id === "string"
      ? (entry.detail.lea_id as string)
      : null;
  const leaName = leaIdFromDetail
    ? leaNameById.get(leaIdFromDetail) ?? leaIdFromDetail
    : null;
  // System actors are fully described by the badge; no second line.
  // Operator actors get the email below the badge so an incident
  // ticket can be traced back to the human who triggered the action.
  const actorDetail =
    entry.actor_kind === "system" ? null : entry.actor_email ?? "Operator";
  const actorTone = entry.actor_kind === "system" ? "ghost" : "mute";

  return (
    <tr>
      <td>
        <div className="cell-stack">
          <span style={{ fontSize: 12.5 }}>
            {relativeTime(entry.occurred_at)}
          </span>
          <span className="id">
            {new Date(entry.occurred_at).toLocaleTimeString()}
          </span>
        </div>
      </td>
      <td>
        {leaName ? (
          <span
            className="name"
            style={{ fontSize: 12.5 }}
            title={leaIdFromDetail ?? undefined}
          >
            {leaName}
          </span>
        ) : (
          <span style={{ color: "var(--ink-4)" }}>—</span>
        )}
      </td>
      <td>
        <DsBadge tone={toneForTimelineSource(entry.source)}>
          {labelForTimelineSource(entry.source)}
        </DsBadge>
      </td>
      <td>
        <div className="cell-stack">
          <span style={{ fontSize: 12.5, fontWeight: 500 }} title={entry.action}>
            {labelForTimelineAction(entry.action)}
          </span>
          {entry.reason && (
            <span
              style={{ fontSize: 11.5, color: "var(--ink-2)" }}
              title={entry.reason}
            >
              {humanizeReason(entry.reason)}
            </span>
          )}
          {entry.detail && <AuditDetailLine detail={entry.detail} />}
        </div>
      </td>
      <td>
        <DsBadge tone={actorTone}>
          {entry.actor_kind === "system" ? "System" : "Operator"}
        </DsBadge>
        {actorDetail && (
          <div
            className="mono"
            style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 2 }}
          >
            {actorDetail}
          </div>
        )}
      </td>
    </tr>
  );
}

function AuditDetailLine({
  detail,
}: {
  detail: Record<string, unknown>;
}) {
  const pieces: string[] = [];
  if (typeof detail.partner === "string") {
    pieces.push(labelForPartner(detail.partner));
  }
  if (
    typeof detail.entity_type === "string" &&
    typeof detail.entity_id === "string"
  ) {
    pieces.push(
      `${labelForEntityType(detail.entity_type)} ${detail.entity_id}`,
    );
  }
  if (typeof detail.event_count === "number") {
    pieces.push(
      `${detail.event_count} event${detail.event_count === 1 ? "" : "s"}`,
    );
  }
  if (typeof detail.snapshots_restored === "number") {
    pieces.push(
      `${detail.snapshots_restored} snapshot${detail.snapshots_restored === 1 ? "" : "s"} restored`,
    );
  }
  if (typeof detail.drift_count === "number" && detail.drift_count > 0) {
    pieces.push(`${detail.drift_count} drift entry`);
  }
  if (detail.forced === true) {
    pieces.push("forced");
  }
  if (pieces.length === 0) return null;
  return (
    <span
      style={{ fontSize: 11.5, color: "var(--ink-3)" }}
      title={JSON.stringify(detail, null, 2)}
    >
      {pieces.join(" · ")}
    </span>
  );
}

function humanizeReason(raw: string): string {
  if (/^L\d+:[A-Z0-9_]+/.test(raw.trim())) {
    return summarizeErrorSummary(raw);
  }
  return raw;
}

function relativeTime(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  const minutes = ms / 60_000;
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${Math.round(minutes)}m ago`;
  const hours = minutes / 60;
  if (hours < 24) return `${Math.round(hours)}h ago`;
  const days = hours / 24;
  if (days < 2) return "Yesterday";
  return `${Math.round(days)}d ago`;
}
