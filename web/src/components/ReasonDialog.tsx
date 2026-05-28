/**
 * Reason-input dialog factory for operator audit ceremonies.
 *
 * Retry, Revert, Reject, Release, Authorize, Revoke, Rotate, and
 * Adjust all write an audit row that cites the operator and a
 * free-text reason. This factory builds a dialog instance whose
 * body is a focused textarea + optional "forced" checkbox + Cancel
 * / Confirm footer.
 */

import { useEffect, useState } from "react";

import {
  createManagedDialog,
  type ManagedDialogContext,
  type ManagedDialogHandle,
} from "@/components/ManagedDialog";

export interface ReasonDialogResult {
  reason: string;
  forced: boolean;
}

export interface ReasonDialogConfig {
  title: string;
  subtitle?: string;
  description?: string;
  defaultReason?: string;
  confirmLabel?: string;
  confirmPalette?: "blue" | "red" | "purple" | "green" | "orange";
  showForced?: boolean;
  forcedLabel?: string;
  forcedDefault?: boolean;
}

export type ReasonDialogValue<T> = T & { config: ReasonDialogConfig };

export type ReasonDialogHandle<T> = ManagedDialogHandle<
  ReasonDialogValue<T>,
  ReasonDialogResult
>;

export function createReasonDialog<T = unknown>(): ReasonDialogHandle<T> {
  return createManagedDialog<ReasonDialogValue<T>, ReasonDialogResult>({
    size: "md",
    title: (v) => v.config.title,
    subtitle: (v) => v.config.subtitle ?? "",
    body: (v, ctx) => <ReasonDialogBody config={v.config} ctx={ctx} />,
  });
}

interface BodyProps {
  config: ReasonDialogConfig;
  ctx: ManagedDialogContext<ReasonDialogResult>;
}

export function ReasonDialogBody({ config, ctx }: BodyProps) {
  const {
    description,
    defaultReason = "",
    confirmLabel = "Confirm",
    confirmPalette = "blue",
    showForced = false,
    forcedLabel = "Force action on a non-failed sync",
    forcedDefault = false,
  } = config;

  const [reason, setReason] = useState(defaultReason);
  const [forced, setForced] = useState(forcedDefault);

  useEffect(() => {
    setReason(defaultReason);
    setForced(forcedDefault);
  }, [defaultReason, forcedDefault]);

  const reasonValid = reason.trim().length > 0;

  const btnClass =
    confirmPalette === "red" ? "ds-btn danger" : "ds-btn primary";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {/* Audit notice */}
      <div className="ds-dialog-notice">
        <div className="lbl">Audited action</div>
        The reason you enter is written to the audit log with your operator
        identity and a UTC timestamp. Reason is required by API.
      </div>

      {description && (
        <div style={{ fontSize: 12.5, color: "var(--ink-2)" }}>
          {description}
        </div>
      )}

      <label className="field" style={{ display: "block", marginBottom: 0 }}>
        <span
          style={{
            display: "block",
            fontSize: 12,
            fontWeight: 500,
            color: "var(--ink)",
            marginBottom: 4,
          }}
        >
          Reason (required)
        </span>
        <textarea
          autoFocus
          rows={3}
          placeholder="e.g. EdLink returned HTTP 503; retrying"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          style={{
            width: "100%",
            background: "var(--panel)",
            border: "1px solid var(--rule-strong)",
            borderRadius: 6,
            padding: "8px 10px",
            fontSize: 12.5,
            color: "var(--ink)",
            fontFamily: "inherit",
            resize: "vertical",
            minHeight: 70,
          }}
        />
        <span
          style={{
            fontSize: 11.5,
            color: "var(--ink-3)",
            marginTop: 4,
            display: "block",
          }}
        >
          Written verbatim to the audit row. Be specific enough that a
          future operator understands the why.
        </span>
      </label>

      {showForced && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            fontSize: 12,
            color: "var(--ink-2)",
          }}
        >
          <input
            type="checkbox"
            checked={forced}
            onChange={(e) => setForced(e.target.checked)}
          />
          <label>{forcedLabel}</label>
        </div>
      )}

      {/* Footer actions */}
      <div
        style={{
          display: "flex",
          gap: 8,
          justifyContent: "flex-end",
          paddingTop: 8,
          borderTop: "1px solid var(--rule)",
        }}
      >
        <button className="ds-btn subtle" type="button" onClick={ctx.close}>
          Cancel
        </button>
        <button
          className={btnClass}
          type="button"
          disabled={!reasonValid}
          onClick={() => {
            ctx.confirm({ reason: reason.trim(), forced });
          }}
          style={!reasonValid ? { opacity: 0.5, cursor: "not-allowed" } : {}}
        >
          {confirmLabel}
        </button>
      </div>
    </div>
  );
}
