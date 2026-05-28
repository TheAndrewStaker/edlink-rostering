/**
 * Thin HTTP client over the EdLink rostering admin API.
 *
 * Bearer-token auth: the dev persona switcher (Phase 1.5b Step 9)
 * mints an HS256 JWT against `DEV_JWT_SECRET` and stores it under
 * `edlink.jwt`. Production swaps the minter for an IdP login flow.
 * Every request carries `Authorization: Bearer <jwt>` so the API's
 * `current_operator` dependency can resolve the operator.
 *
 * 401 handling: when the API rejects a request as unauthenticated
 * (expired, malformed, or revoked token), the stored JWT is cleared
 * and the auth listener is notified so the app root swaps back to
 * the SignInScreen. Without this the panels would render inline
 * "Token expired" error strings instead of routing the operator
 * back to the persona picker.
 */

const JWT_STORAGE_KEY = "edlink.jwt";

// Lazy import to avoid a static cycle: useAuth.ts imports from this
// module (`getJwt`), and this module needs to notify the auth listener
// on 401. The handler is wired by `useAuth.ts` at module load.
let unauthorizedHandler: (() => void) | null = null;

export function setUnauthorizedHandler(handler: () => void): void {
  unauthorizedHandler = handler;
}

export function getJwt(): string | null {
  return localStorage.getItem(JWT_STORAGE_KEY);
}

export function setJwt(value: string): void {
  localStorage.setItem(JWT_STORAGE_KEY, value);
}

export function clearJwt(): void {
  localStorage.removeItem(JWT_STORAGE_KEY);
}

async function request<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json");
  const jwt = getJwt();
  if (jwt) {
    headers.set("Authorization", `Bearer ${jwt}`);
  }
  const response = await fetch(path, { ...init, headers });
  if (!response.ok) {
    // Clone before parsing so the text() fallback below doesn't fail
    // with "body stream already read" when the response wasn't JSON.
    const errorBody = await response.clone().text();
    let detail = errorBody;
    try {
      const parsed = JSON.parse(errorBody) as { detail?: string };
      detail = parsed.detail ?? errorBody;
    } catch {
      // Body wasn't JSON; keep the plain text.
    }
    if (response.status === 401) {
      clearJwt();
      unauthorizedHandler?.();
    }
    throw new ApiError(response.status, detail || response.statusText);
  }
  return (await response.json()) as T;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export interface LeaSummary {
  id: string;
  name: string;
  lea_type: string;
  state: string;
  student_count: number;
  enrollment_count: number;
  latest_sync_at: string | null;
  latest_sync_status: string | null;
  cursor_lag_days: number | null;
  in_flight_count: number;
}

export interface TestEventScenario {
  id: string;
  label: string;
  section: string;
  kind: string;
  description: string;
}

export interface TestEventCatalog {
  scenarios: TestEventScenario[];
}

export interface TestEventDispatchResponse {
  sync_job_id: string;
  scenario_id: string;
  lea_id: string;
  running_visibility_seconds: number;
}

export interface SyncActivityBucket {
  hour: string;
  success: number;
  warning: number;
  failed: number;
}

export interface SyncJobSummary {
  id: string;
  lea_id: string;
  partner: string;
  status: string;
  started_at: string;
  completed_at: string | null;
  event_count: number;
  error_count: number;
  warning_count: number;
  cursor_before: string | null;
  cursor_after: string | null;
  error_summary: string | null;
}

export interface ValidationIssueRow {
  layer: number;
  code: string;
  payload_reference: string | null;
  detail: Record<string, unknown> | null;
  created_at: string;
}

export interface RevertHistoryRow {
  id: string;
  operator_identity: string;
  reason: string;
  reverted_at: string;
  snapshots_restored: number;
}

export interface RetryHistoryRow {
  id: string;
  operator_identity: string;
  reason: string;
  retried_at: string;
  cursor_rewound_to: string | null;
  forced: boolean;
}

export interface SyncJobDetail {
  sync: SyncJobSummary;
  validation_issues: ValidationIssueRow[];
  quarantined_entity_ids: string[];
  revert_history: RevertHistoryRow[];
  retry_history: RetryHistoryRow[];
}

export interface CursorStateRow {
  lea_id: string;
  partner: string;
  last_event_id: string | null;
  last_event_at: string | null;
  last_poll_at: string | null;
  cold_start_required: boolean;
  days_behind: number | null;
}

export interface QuarantineRowOut {
  id: string;
  sync_job_id: string;
  lea_id: string;
  entity_type: string;
  entity_id: string;
  reason: string;
  created_at: string;
  resolved_at: string | null;
  resolution_status: string | null;
  resolution_operator: string | null;
}

export interface AlertOut {
  code: string;
  severity: string;
  dedup_key: string;
  lea_id: string | null;
  measurements: Record<string, number>;
  properties: Record<string, string>;
}

export interface RetryResponse {
  sync_job_id: string;
  lea_id: string;
  partner: string;
  cursor_rewound_to: string | null;
  forced: boolean;
}

export interface RevertResponse {
  sync_job_id: string;
  revert_generation_id: string;
  snapshots_restored: number;
  canonical_rows_updated: number;
  canonical_rows_soft_deleted: number;
}

export interface QuarantineReleaseResponse {
  quarantine_id: string;
  release_generation_id: string;
  entity_type: string;
  entity_id: string;
}

export interface QuarantineRejectResponse {
  quarantine_id: string;
  rejected_at: string;
}

export interface ConnectorAuthorizationOut {
  id: string;
  lea_id: string;
  lea_name: string;
  partner: string;
  status: string;
  authorized_at: string | null;
  authorized_by_email: string | null;
  revoked_at: string | null;
  revoked_by_email: string | null;
  secret_ref: string;
  poll_interval_seconds: number;
  notes: string | null;
  integration_status: string;
  sharing_scope: string | null;
  integration_status_observed_at: string | null;
}

export interface ConnectorListQuery {
  lea_id?: string;
  include_revoked?: boolean;
}

export interface ConnectorAuthorizeResponse {
  id: string;
  lea_id: string;
  partner: string;
  status: string;
  secret_ref: string;
  poll_interval_seconds: number;
  created_new_row: boolean;
}

export interface ConnectorRevokeResponse {
  id: string;
  lea_id: string;
  partner: string;
  revoked_at: string;
}

export interface ConnectorRotateCredentialResponse {
  id: string;
  lea_id: string;
  partner: string;
  previous_secret_ref: string;
  new_secret_ref: string;
}

export interface ConnectorAdjustPollIntervalResponse {
  id: string;
  lea_id: string;
  partner: string;
  previous_poll_interval_seconds: number;
  new_poll_interval_seconds: number;
}

export interface ReconciliationDriftDetailOut {
  entity_type: string;
  canonical_only_ids: string[];
  partner_only_ids: string[];
  canonical_mid_hash: string;
  partner_mid_hash: string;
}

export interface TimelineEntryOut {
  id: string;
  source: string;
  occurred_at: string;
  actor_kind: "operator" | "system";
  actor_email: string | null;
  action: string;
  reason: string | null;
  target_kind: string;
  target_id: string;
  detail: Record<string, unknown> | null;
}

export interface AuditCursor {
  occurred_at: string;
  id: string;
}

export interface AuditExplorerPage {
  entries: TimelineEntryOut[];
  next_cursor: AuditCursor | null;
}

export interface AuditExplorerQuery {
  operator_id?: string;
  action_prefix?: string;
  since?: string;
  until?: string;
  cursor_occurred_at?: string;
  cursor_id?: string;
  limit?: number;
}

export interface ReconciliationRunRow {
  id: string;
  lea_id: string;
  partner: string;
  started_at: string;
  completed_at: string;
  status: string;
  canonical_root_hash: string;
  partner_root_hash: string | null;
  drift: ReconciliationDriftDetailOut[];
  error_message: string | null;
}

export const api = {
  listLeas: () => request<LeaSummary[]>("/api/v1/leas"),
  syncActivity: () => request<SyncActivityBucket[]>("/api/v1/syncs/activity"),
  listSyncs: (lea_id: string, limit = 20) =>
    request<SyncJobSummary[]>(
      `/api/v1/leas/${encodeURIComponent(lea_id)}/syncs?limit=${limit}`,
    ),
  getSync: (sync_job_id: string) =>
    request<SyncJobDetail>(
      `/api/v1/syncs/${encodeURIComponent(sync_job_id)}`,
    ),
  listCursors: (lea_id?: string) =>
    request<CursorStateRow[]>(
      lea_id
        ? `/api/v1/cursors?lea_id=${encodeURIComponent(lea_id)}`
        : `/api/v1/cursors`,
    ),
  listQuarantine: (lea_id?: string) =>
    request<QuarantineRowOut[]>(
      lea_id
        ? `/api/v1/quarantine?lea_id=${encodeURIComponent(lea_id)}`
        : `/api/v1/quarantine`,
    ),
  listAlerts: () => request<AlertOut[]>("/api/v1/alerts"),
  listReconciliationRuns: (lea_id: string, limit = 20) =>
    request<ReconciliationRunRow[]>(
      `/api/v1/leas/${encodeURIComponent(lea_id)}/reconciliation?limit=${limit}`,
    ),
  listLeaTimeline: (lea_id: string, limit = 50) =>
    request<TimelineEntryOut[]>(
      `/api/v1/leas/${encodeURIComponent(lea_id)}/timeline?limit=${limit}`,
    ),
  listAuditEntries: (query: AuditExplorerQuery = {}) => {
    const params = new URLSearchParams();
    if (query.operator_id) params.set("operator_id", query.operator_id);
    if (query.action_prefix)
      params.set("action_prefix", query.action_prefix);
    if (query.since) params.set("since", query.since);
    if (query.until) params.set("until", query.until);
    if (query.cursor_occurred_at)
      params.set("cursor_occurred_at", query.cursor_occurred_at);
    if (query.cursor_id) params.set("cursor_id", query.cursor_id);
    if (query.limit != null) params.set("limit", String(query.limit));
    const qs = params.toString();
    return request<AuditExplorerPage>(
      `/api/v1/admin/audit${qs ? `?${qs}` : ""}`,
    );
  },

  retrySync: (sync_job_id: string, reason: string, forced: boolean) =>
    request<RetryResponse>(
      `/api/v1/syncs/${encodeURIComponent(sync_job_id)}/retry`,
      {
        method: "POST",
        body: JSON.stringify({ reason, forced }),
      },
    ),
  revertSync: (sync_job_id: string, reason: string) =>
    request<RevertResponse>(
      `/api/v1/syncs/${encodeURIComponent(sync_job_id)}/revert`,
      {
        method: "POST",
        body: JSON.stringify({ reason }),
      },
    ),
  releaseQuarantine: (quarantine_id: string) =>
    request<QuarantineReleaseResponse>(
      `/api/v1/quarantine/${encodeURIComponent(quarantine_id)}/release`,
      {
        method: "POST",
        body: JSON.stringify({}),
      },
    ),
  rejectQuarantine: (quarantine_id: string, reason: string) =>
    request<QuarantineRejectResponse>(
      `/api/v1/quarantine/${encodeURIComponent(quarantine_id)}/reject`,
      {
        method: "POST",
        body: JSON.stringify({ reason }),
      },
    ),

  listConnectors: (query: ConnectorListQuery = {}) => {
    const params = new URLSearchParams();
    if (query.lea_id) params.set("lea_id", query.lea_id);
    if (query.include_revoked) params.set("include_revoked", "true");
    const qs = params.toString();
    return request<ConnectorAuthorizationOut[]>(
      `/api/v1/connectors${qs ? `?${qs}` : ""}`,
    );
  },
  authorizeConnector: (
    lea_id: string,
    partner: string,
    body: {
      secret_ref: string;
      reason: string;
      poll_interval_seconds?: number;
      notes?: string;
    },
  ) =>
    request<ConnectorAuthorizeResponse>(
      `/api/v1/connectors/${encodeURIComponent(lea_id)}/${encodeURIComponent(partner)}/authorize`,
      { method: "POST", body: JSON.stringify(body) },
    ),
  revokeConnector: (lea_id: string, partner: string, reason: string) =>
    request<ConnectorRevokeResponse>(
      `/api/v1/connectors/${encodeURIComponent(lea_id)}/${encodeURIComponent(partner)}/revoke`,
      { method: "POST", body: JSON.stringify({ reason }) },
    ),
  rotateConnectorCredential: (
    lea_id: string,
    partner: string,
    new_secret_ref: string,
    reason: string,
  ) =>
    request<ConnectorRotateCredentialResponse>(
      `/api/v1/connectors/${encodeURIComponent(lea_id)}/${encodeURIComponent(partner)}/rotate-credential`,
      {
        method: "POST",
        body: JSON.stringify({ new_secret_ref, reason }),
      },
    ),
  adjustConnectorPollInterval: (
    lea_id: string,
    partner: string,
    new_poll_interval_seconds: number,
    reason: string,
  ) =>
    request<ConnectorAdjustPollIntervalResponse>(
      `/api/v1/connectors/${encodeURIComponent(lea_id)}/${encodeURIComponent(partner)}/adjust-poll-interval`,
      {
        method: "POST",
        body: JSON.stringify({ new_poll_interval_seconds, reason }),
      },
    ),

  listTestEventScenarios: () =>
    request<TestEventCatalog>("/api/v1/dev/test-events/scenarios"),
  dispatchTestEvent: (lea_id: string, scenario_id: string) =>
    request<TestEventDispatchResponse>("/api/v1/dev/test-events", {
      method: "POST",
      body: JSON.stringify({ lea_id, scenario_id }),
    }),
};
