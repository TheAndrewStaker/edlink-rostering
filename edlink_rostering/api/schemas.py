"""Response and request models for the admin API.

Pydantic models keep the JSON contract explicit so the React app can
generate types from them. Models are intentionally close to the
database rows; nothing fancier than that is needed for the admin app.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class LeaSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    lea_type: str
    state: str
    status: str
    student_count: int
    enrollment_count: int
    latest_sync_at: datetime | None
    latest_sync_status: str | None
    cursor_lag_days: float | None
    in_flight_count: int = 0


class LeaCreateRequest(BaseModel):
    """Request body for ``POST /api/v1/leas``.

    ``id`` is the canonical LEA identifier the rest of the system
    threads through every query and audit row. Onboarding picks a
    stable slug (e.g. ``lea-acme-usd``) up front so downstream tables
    can pin it without rewrites.
    """

    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    lea_type: str = Field(min_length=1, max_length=64)
    state: str = Field(min_length=2, max_length=2)
    timezone: str = Field(default="America/New_York", min_length=1, max_length=64)
    nces_lea_id: str | None = Field(default=None, max_length=32)
    edlink_integration_id: str | None = Field(default=None, max_length=128)


class LeaCreateResponse(BaseModel):
    """Response returned by ``POST /api/v1/leas``."""

    id: str
    name: str
    lea_type: str
    state: str
    timezone: str
    nces_lea_id: str | None
    edlink_integration_id: str | None
    status: str


class LeaStatusTransitionRequest(BaseModel):
    """Request body for ``PATCH /api/v1/leas/{lea_id}/status``."""

    status: str = Field(min_length=1, max_length=32)
    reason: str = Field(min_length=1)


class LeaStatusTransitionResponse(BaseModel):
    """Response returned by ``PATCH /api/v1/leas/{lea_id}/status``."""

    id: str
    status: str
    previous_status: str


class SyncJobSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    lea_id: str
    partner: str
    status: str
    started_at: datetime
    completed_at: datetime | None
    event_count: int
    error_count: int
    warning_count: int
    cursor_before: str | None
    cursor_after: str | None
    error_summary: str | None


class SyncActivityBucket(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    hour: datetime
    success: int
    warning: int
    failed: int


class ValidationIssueRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    layer: int
    code: str
    payload_reference: str | None
    detail: dict[str, object] | None
    created_at: datetime


class SyncJobDetail(BaseModel):
    sync: SyncJobSummary
    validation_issues: list[ValidationIssueRow]
    quarantined_entity_ids: list[str]
    revert_history: list["RevertHistoryRow"]
    retry_history: list["RetryHistoryRow"]


class RevertHistoryRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    operator_identity: str
    reason: str
    reverted_at: datetime
    snapshots_restored: int


class RetryHistoryRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    operator_identity: str
    reason: str
    retried_at: datetime
    cursor_rewound_to: str | None
    forced: bool


class CursorStateRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    lea_id: str
    partner: str
    last_event_id: str | None
    last_event_at: datetime | None
    last_poll_at: datetime | None
    cold_start_required: bool
    days_behind: float | None


class QuarantineRowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    sync_job_id: uuid.UUID
    lea_id: str
    entity_type: str
    entity_id: str
    reason: str
    created_at: datetime
    resolved_at: datetime | None
    resolution_status: str | None
    resolution_operator: str | None


class AlertOut(BaseModel):
    code: str
    severity: str
    dedup_key: str
    lea_id: str | None
    measurements: dict[str, float]
    properties: dict[str, str]


class RetryRequest(BaseModel):
    reason: str = Field(min_length=1)
    forced: bool = False


class RevertRequest(BaseModel):
    reason: str = Field(min_length=1)


class QuarantineRejectRequest(BaseModel):
    reason: str = Field(min_length=1)


class RetryResponse(BaseModel):
    sync_job_id: uuid.UUID
    lea_id: str
    partner: str
    cursor_rewound_to: str | None
    forced: bool


class RevertResponse(BaseModel):
    sync_job_id: uuid.UUID
    revert_generation_id: uuid.UUID
    snapshots_restored: int
    canonical_rows_updated: int
    canonical_rows_soft_deleted: int


class QuarantineReleaseResponse(BaseModel):
    quarantine_id: uuid.UUID
    release_generation_id: uuid.UUID
    entity_type: str
    entity_id: str


class QuarantineRejectResponse(BaseModel):
    quarantine_id: uuid.UUID
    rejected_at: datetime


class ConnectorAuthorizationOut(BaseModel):
    """One row in the integrations roll-up table.

    ``integration_status`` and ``sharing_scope`` reflect EdLink's
    per-integration state polled on every sync drain by the
    :class:`~edlink_rostering.services.integration_status.IntegrationStatusPoller`.
    Degraded ``integration_status`` values
    (``inactive``/``disabled``/``destroyed``) drive the
    ``integration_degraded`` alert; the admin app shows the raw value
    so on-call sees the partner-side enum directly.
    """

    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    lea_id: str
    lea_name: str
    partner: str
    status: str
    authorized_at: datetime | None
    authorized_by_email: str | None
    revoked_at: datetime | None
    revoked_by_email: str | None
    secret_ref: str
    poll_interval_seconds: int
    notes: str | None
    integration_status: str
    sharing_scope: str | None
    integration_status_observed_at: datetime | None


class ConnectorAuthorizeRequest(BaseModel):
    secret_ref: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    poll_interval_seconds: int | None = Field(default=None, ge=60, le=3600)
    notes: str | None = None


class ConnectorAuthorizeResponse(BaseModel):
    id: uuid.UUID
    lea_id: str
    partner: str
    status: str
    secret_ref: str
    poll_interval_seconds: int
    created_new_row: bool


class ConnectorRevokeRequest(BaseModel):
    reason: str = Field(min_length=1)


class ConnectorRevokeResponse(BaseModel):
    id: uuid.UUID
    lea_id: str
    partner: str
    revoked_at: datetime


class ConnectorRotateCredentialRequest(BaseModel):
    new_secret_ref: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class ConnectorRotateCredentialResponse(BaseModel):
    id: uuid.UUID
    lea_id: str
    partner: str
    previous_secret_ref: str
    new_secret_ref: str


class ConnectorAdjustPollIntervalRequest(BaseModel):
    new_poll_interval_seconds: int = Field(ge=60, le=3600)
    reason: str = Field(min_length=1)


class ConnectorAdjustPollIntervalResponse(BaseModel):
    id: uuid.UUID
    lea_id: str
    partner: str
    previous_poll_interval_seconds: int
    new_poll_interval_seconds: int


class TimelineEntryOut(BaseModel):
    """One row in the activity timeline (per-LEA or cross-LEA).

    The shape mirrors :class:`edlink_rostering.services.admin_timeline.TimelineEntry`.
    ``id`` is unique across the UNION (the source table's primary
    key for most branches, with ``#created`` / ``#resolved`` suffixes
    on the quarantine branch). ``occurred_at`` is the timestamp the
    timeline orders by.

    Cross-LEA queries surface ``lea_id`` inside ``detail`` for every
    branch so the founder explorer can render the LEA column without
    a separate join.
    """

    id: str
    source: str
    occurred_at: datetime
    actor_kind: str
    actor_email: str | None
    action: str
    reason: str | None
    target_kind: str
    target_id: str
    detail: dict[str, object] | None


class AuditExplorerPage(BaseModel):
    """One page of the cross-LEA audit explorer.

    ``entries`` is the slice of timeline entries newest-first.
    ``next_cursor`` is the (occurred_at, id) pair the client passes
    on the next request to fetch the following page; ``None`` when
    no more entries exist.
    """

    entries: list[TimelineEntryOut]
    next_cursor: "AuditCursor | None" = None


class AuditCursor(BaseModel):
    """Opaque next-page cursor for the audit explorer."""

    occurred_at: datetime
    id: str


class ReconciliationDriftDetailOut(BaseModel):
    """One per-entity-type drift entry inside a reconciliation_runs row."""

    entity_type: str
    canonical_only_ids: list[str]
    partner_only_ids: list[str]
    canonical_mid_hash: str
    partner_mid_hash: str


class ReconciliationRunRow(BaseModel):
    """One ``reconciliation_runs`` row, shaped for the per-LEA drawer panel."""

    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    lea_id: str
    partner: str
    started_at: datetime
    completed_at: datetime
    status: str
    canonical_root_hash: str
    partner_root_hash: str | None
    drift: list[ReconciliationDriftDetailOut]
    error_message: str | None


SyncJobDetail.model_rebuild()
AuditExplorerPage.model_rebuild()


__all__ = [
    "AlertOut",
    "AuditCursor",
    "AuditExplorerPage",
    "ConnectorAdjustPollIntervalRequest",
    "ConnectorAdjustPollIntervalResponse",
    "ConnectorAuthorizationOut",
    "ConnectorAuthorizeRequest",
    "ConnectorAuthorizeResponse",
    "ConnectorRevokeRequest",
    "ConnectorRevokeResponse",
    "ConnectorRotateCredentialRequest",
    "ConnectorRotateCredentialResponse",
    "CursorStateRow",
    "LeaCreateRequest",
    "LeaCreateResponse",
    "LeaStatusTransitionRequest",
    "LeaStatusTransitionResponse",
    "LeaSummary",
    "QuarantineRejectRequest",
    "QuarantineRejectResponse",
    "QuarantineReleaseResponse",
    "QuarantineRowOut",
    "ReconciliationDriftDetailOut",
    "ReconciliationRunRow",
    "RetryHistoryRow",
    "RetryRequest",
    "RetryResponse",
    "RevertHistoryRow",
    "RevertRequest",
    "RevertResponse",
    "SyncJobDetail",
    "SyncJobSummary",
    "TimelineEntryOut",
    "ValidationIssueRow",
]
