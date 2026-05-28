"""Pipeline orchestrator for the five validation layers.

One entry point: :func:`run_pipeline`. Returns a :class:`ValidationReport`
that the sync worker reads to decide:

- Whether the batch can commit at all (Layer 1 failure aborts the page).
- Which events should be routed to ``quarantine`` rather than canonical
  (Layer 4 referential violations).
- Which issues to persist into ``sync_validation_results`` for operator
  visibility.

Each event is annotated with its outcome on the report; the worker can
look up an event by ``event_id`` and find out whether it cleared validation,
got quarantined, or hit an error that fails the page.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from edlink_rostering.connectors.protocol import EventPage
from edlink_rostering.core.types import LeaId, StudentId
from edlink_rostering.events.envelope import NormalizedEvent
from edlink_rostering.services.validation._issues import (
    Severity,
    ValidationIssue,
)
from edlink_rostering.services.validation.layer_2_schema import check_schema
from edlink_rostering.services.validation.layer_3_parse import check_parse
from edlink_rostering.services.validation.layer_4_referential import (
    check_referential,
)
from edlink_rostering.services.validation.layer_5_thresholds import (
    check_thresholds,
)


@dataclass(frozen=True)
class LEAState:
    """LEA-scoped read-side state passed to Layers 4 and 5.

    ``known_student_ids`` feeds Layer 4: enrollment->student resolution
    walks (already-in-canonical) plus (created-earlier-in-this-batch).

    The remaining fields feed Layer 5 thresholds. ``live_student_count``
    is the current live student count (canonical, ``deleted_at IS NULL``);
    Layer 5 uses it to compute population shift if the page lands.
    ``recent_event_counts`` are ``event_count`` values from the last N
    successful sync_jobs for this LEA, used as the baseline for the
    change-event volume threshold. Both default to empty so DB-free
    Layer 1-4 tests do not need to plumb history.
    """

    lea_id: LeaId
    known_student_ids: set[StudentId] = field(default_factory=set)
    live_student_count: int = 0
    recent_event_counts: tuple[int, ...] = ()
    recent_deletion_counts: tuple[int, ...] = ()


@dataclass(frozen=True)
class ValidationReport:
    """Outcome of running the five layers against a page.

    ``page_blocked`` is true when at least one Layer 1, Layer 2, or
    Layer 5 error fired. The worker fails the sync job with status
    ``failed`` instead of writing snapshots.

    ``quarantined_event_ids`` are the events Layer 4 routed to
    quarantine. The worker MUST write them to the ``quarantine`` table
    and skip the canonical/snapshot writes for them.

    ``ok_event_ids`` are the events that cleared all five layers and
    should be written to snapshots and canonical.

    Issues are ordered by event then by layer for deterministic
    presentation in the CLI.
    """

    issues: tuple[ValidationIssue, ...]
    ok_event_ids: tuple[str, ...]
    quarantined_event_ids: tuple[str, ...]
    page_blocked: bool

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == Severity.ERROR)

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == Severity.WARNING)


def run_pipeline(
    page: EventPage,
    lea_state: LEAState,
) -> ValidationReport:
    """Run Layers 1-5 against ``page``.

    Layers run in order. Layer 1 is read directly off ``page``; the rest
    operate on the normalized events. Layer 4 is the only layer that
    routes events (to quarantine) rather than failing them outright.
    """

    issues: list[ValidationIssue] = []
    quarantined: set[str] = set()
    rejected: set[str] = set()

    # Layer 1: HTTP integrity, read from the connector boundary.
    layer_1 = page.layer_1_check
    if not layer_1.ok:
        issues.append(
            ValidationIssue(
                layer=1,
                code="HTTP_INTEGRITY_FAILED",
                severity=Severity.ERROR,
                event_id=None,
                detail={
                    "http_status": layer_1.http_status,
                    "content_type": layer_1.content_type,
                    "body_well_formed": layer_1.body_well_formed,
                    "error": layer_1.error,
                },
            )
        )

    # Short-circuit subsequent layers on Layer 1 failure: there is nothing
    # trustworthy to validate.
    if layer_1.ok:
        # Layer 2: schema integrity per event.
        for event in page.events:
            for issue in check_schema(event):
                issues.append(issue)
                rejected.add(event.event_id)

        # Layer 3: parse-time per event. Skip events already rejected.
        for event in page.events:
            if event.event_id in rejected:
                continue
            for issue in check_parse(event):
                issues.append(issue)
                rejected.add(event.event_id)

        # Layer 4: referential. Operates on the surviving events as a set.
        # Builds the per-batch known-student-ids by walking
        # student.created events in order, then routes orphan enrollments
        # to quarantine.
        surviving: list[NormalizedEvent] = [
            e for e in page.events if e.event_id not in rejected
        ]
        layer_4_issues, quarantine_ids = check_referential(
            surviving, lea_state
        )
        issues.extend(layer_4_issues)
        quarantined.update(quarantine_ids)

        # Layer 5: thresholds. Currently a stub that emits an informational
        # measurement; the worker will surface it through telemetry.
        layer_5_issues = check_thresholds(page, lea_state)
        issues.extend(layer_5_issues)

    page_blocked = any(
        i.severity == Severity.ERROR and i.event_id is None for i in issues
    ) or not layer_1.ok

    ok_event_ids = tuple(
        e.event_id
        for e in page.events
        if e.event_id not in rejected and e.event_id not in quarantined
    )

    return ValidationReport(
        issues=tuple(issues),
        ok_event_ids=ok_event_ids,
        quarantined_event_ids=tuple(sorted(quarantined)),
        page_blocked=page_blocked,
    )


