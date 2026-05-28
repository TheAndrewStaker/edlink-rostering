import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api, SyncActivityBucket } from "@/api/client";
import { DsBadge } from "@/components/DsBadge";

function bucketToSlots(bucket: SyncActivityBucket): string[] {
  const slots: string[] = [];
  for (let i = 0; i < bucket.success; i++) slots.push("ok");
  for (let i = 0; i < bucket.warning; i++) slots.push("warn");
  for (let i = 0; i < bucket.failed; i++) slots.push("bad");
  return slots;
}

interface HourCell {
  hour: number;
  bucket: SyncActivityBucket | null;
  slots: string[];
}

function buildHours(buckets: SyncActivityBucket[]): HourCell[] {
  const now = new Date();
  const hourMap = new Map<number, SyncActivityBucket>();
  for (const b of buckets) {
    const h = new Date(b.hour).getHours();
    hourMap.set(h, b);
  }
  const startHour = (now.getHours() + 1) % 24;
  return Array.from({ length: 24 }, (_, i) => {
    const hour = (startHour + i) % 24;
    const bucket = hourMap.get(hour) ?? null;
    return {
      hour,
      bucket,
      slots: bucket ? bucketToSlots(bucket) : [],
    };
  });
}

function formatHourLabel(hour: number): string {
  const suffix = hour < 12 ? "am" : "pm";
  const display = hour % 12 === 0 ? 12 : hour % 12;
  return `${display}:00${suffix}`;
}

function axisLabel(startHour: number, i: number): string {
  const hour = (startHour + i) % 24;
  if (i % 6 === 0) return formatHourLabel(hour);
  return "";
}

// The hover tooltip is ~180px wide and a 24-bar grid leaves each bar
// far narrower than that at any viewport width. A center-anchored
// tooltip clips on the side that runs off the chart edge. Anchor
// every bar to the side of the chart with more room: bars in the
// left half extend their tooltip rightward, bars in the right half
// extend leftward. This stays correct as the viewport narrows.
function tipAnchor(i: number): "tip-left" | "tip-right" {
  return i < 12 ? "tip-left" : "tip-right";
}

export function SyncActivityRail() {
  const { data: buckets } = useQuery({
    queryKey: ["sync-activity"],
    queryFn: api.syncActivity,
    refetchInterval: 60_000,
  });
  const [hoveredIdx, setHoveredIdx] = useState<number | null>(null);

  const hours = buildHours(buckets ?? []);
  const startHour = (new Date().getHours() + 1) % 24;

  const totalSuccess = (buckets ?? []).reduce((s, b) => s + b.success, 0);
  const totalWarning = (buckets ?? []).reduce((s, b) => s + b.warning, 0);
  const totalFailed = (buckets ?? []).reduce((s, b) => s + b.failed, 0);
  const totalSyncs = totalSuccess + totalWarning + totalFailed;

  return (
    <div className="ds-panel">
      <div className="ds-panel-head">
        <div>
          <h3>Sync activity, last 24h</h3>
          <div className="sub">Per-hour stacked outcome by LEA.</div>
        </div>
        <span className="count">{totalSyncs} syncs</span>
      </div>
      <div className="sync-rail">
        {hours.map((cell, i) => {
          const success = cell.bucket?.success ?? 0;
          const warning = cell.bucket?.warning ?? 0;
          const failed = cell.bucket?.failed ?? 0;
          return (
            <div
              key={i}
              className="h"
              onMouseEnter={() => setHoveredIdx(i)}
              onMouseLeave={() =>
                setHoveredIdx((prev) => (prev === i ? null : prev))
              }
            >
              <div className="stack">
                {cell.slots.map((s, j) => (
                  <div key={j} className={`seg ${s}`} />
                ))}
              </div>
              {hoveredIdx === i && (
                <div
                  className={`tip ${tipAnchor(i)}`}
                  role="tooltip"
                  aria-label={`Sync activity at ${formatHourLabel(cell.hour)}: ${success} success, ${warning} warning, ${failed} failed`}
                >
                  <span className="when">{formatHourLabel(cell.hour)}</span>
                  {success} success &middot; {warning} warning &middot;{" "}
                  {failed} failed
                </div>
              )}
            </div>
          );
        })}
        <div className="axis">
          {Array.from({ length: 24 }, (_, i) => (
            <span key={i}>{axisLabel(startHour, i)}</span>
          ))}
        </div>
      </div>
      <div
        style={{
          display: "flex",
          gap: 14,
          padding: "8px 16px 14px",
          fontSize: 11.5,
          color: "var(--ink-2)",
        }}
      >
        <span>
          <DsBadge tone="ok" style={{ padding: "1px 6px" }}>
            success
          </DsBadge>{" "}
          {totalSuccess}
        </span>
        <span>
          <DsBadge tone="warn" style={{ padding: "1px 6px" }}>
            warning
          </DsBadge>{" "}
          {totalWarning}
        </span>
        <span>
          <DsBadge tone="bad" style={{ padding: "1px 6px" }}>
            failed
          </DsBadge>{" "}
          {totalFailed}
        </span>
      </div>
    </div>
  );
}
