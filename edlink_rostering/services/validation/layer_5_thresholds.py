"""Layer 5: business-rule threshold validation.

Layer 5 is advisory, not page-blocking. A threshold trip emits a
``WARNING`` issue with a stable code so the alert layer can route it to
operators; the sync worker still commits the page. The reasoning, per
``docs/design/edlink-oneroster-rostering.md``: SIS administrators
sometimes do legitimate bulk operations (graduation, enrollment roll-up,
mass schedule change) and a Layer 5 trip is "look at this", not "stop
the integration". Page-blocking the wrong day breaks district mornings.

Three thresholds ship in session 3:

1. **Change-event volume spike** (``THRESHOLD_EVENT_VOLUME_SPIKE``):
   ``page_event_count > volume_spike_multiplier * max(median, 1)`` where
   median is taken over ``recent_event_counts``. Default multiplier is
   3.0 (a 3x spike vs typical traffic).

2. **Deletion-burst** (``THRESHOLD_DELETION_BURST``): more than
   ``deletion_share_threshold`` of the page's events are deletions AND
   the deletion count exceeds the recent deletion baseline by
   ``deletion_burst_multiplier``. Default thresholds: 30 percent share,
   2x spike. A district end-of-year run might delete 200 students in
   one push; this should warn but not block.

3. **Population shift** (``THRESHOLD_POPULATION_SHIFT``): if the page
   would drop the live student count by more than
   ``population_shift_threshold`` (default 0.25, a 25 percent drop) the
   threshold fires. Computed by adding student creations and
   subtracting student deletions in the page, then comparing the
   projected count against the live baseline.

Until at least ``min_history_for_thresholds`` (default 3) successful
sync_jobs exist for this LEA, the spike thresholds are suppressed and a
``THRESHOLD_BASELINE_INSUFFICIENT`` informational issue is emitted
instead. This keeps the day-one sync (which is necessarily atypical)
from drowning operators in spurious alerts.

The page-observation informational issue from session 2 still fires
every page; the threshold issues fire only when the threshold trips.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import TYPE_CHECKING

from edlink_rostering.canonical.entities import EntityType
from edlink_rostering.connectors.protocol import EventPage
from edlink_rostering.events.envelope import Operation
from edlink_rostering.services.validation._issues import (
    Severity,
    ValidationIssue,
    issue,
)

if TYPE_CHECKING:
    from edlink_rostering.services.validation.pipeline import LEAState


@dataclass(frozen=True)
class ThresholdConfig:
    """Per-LEA tunable thresholds.

    Defaults are conservative starting points. Operations tune these per
    LEA via configuration once real traffic patterns are observed. The
    POC uses defaults uniformly.
    """

    volume_spike_multiplier: float = 3.0
    deletion_share_threshold: float = 0.30
    deletion_burst_multiplier: float = 2.0
    population_shift_threshold: float = 0.25
    min_history_for_thresholds: int = 3


DEFAULT_CONFIG = ThresholdConfig()


def check_thresholds(
    page: EventPage,
    lea_state: "LEAState",
    config: ThresholdConfig = DEFAULT_CONFIG,
) -> list[ValidationIssue]:
    """Run Layer 5 against ``page`` using ``lea_state`` history.

    Always emits ``THRESHOLD_PAGE_OBSERVATION`` so operators can audit
    the numbers Layer 5 saw. Threshold trips emit additional issues.
    """

    student_events = sum(
        1 for e in page.events if e.entity_type == EntityType.STUDENT
    )
    enrollment_events = sum(
        1 for e in page.events if e.entity_type == EntityType.ENROLLMENT
    )
    deletion_events = sum(
        1 for e in page.events if e.operation == Operation.DELETED
    )
    student_creations = sum(
        1
        for e in page.events
        if e.entity_type == EntityType.STUDENT
        and e.operation == Operation.CREATED
    )
    student_deletions = sum(
        1
        for e in page.events
        if e.entity_type == EntityType.STUDENT
        and e.operation == Operation.DELETED
    )
    page_size = len(page.events)

    issues: list[ValidationIssue] = [
        issue(
            layer=5,
            code="THRESHOLD_PAGE_OBSERVATION",
            severity=Severity.WARNING,
            event_id=None,
            detail={
                "lea_id": lea_state.lea_id,
                "page_event_count": page_size,
                "student_events": student_events,
                "enrollment_events": enrollment_events,
                "deletion_events": deletion_events,
                "known_students_in_lea": len(lea_state.known_student_ids),
                "live_student_count": lea_state.live_student_count,
                "history_size": len(lea_state.recent_event_counts),
            },
        )
    ]

    if len(lea_state.recent_event_counts) < config.min_history_for_thresholds:
        issues.append(
            issue(
                layer=5,
                code="THRESHOLD_BASELINE_INSUFFICIENT",
                severity=Severity.WARNING,
                event_id=None,
                detail={
                    "lea_id": lea_state.lea_id,
                    "history_size": len(lea_state.recent_event_counts),
                    "min_required": config.min_history_for_thresholds,
                    "note": (
                        "Spike thresholds suppressed until enough "
                        "history is available."
                    ),
                },
            )
        )
        return issues

    baseline_events = max(1.0, float(median(lea_state.recent_event_counts)))
    spike_limit = baseline_events * config.volume_spike_multiplier
    if page_size > spike_limit:
        issues.append(
            issue(
                layer=5,
                code="THRESHOLD_EVENT_VOLUME_SPIKE",
                severity=Severity.WARNING,
                event_id=None,
                detail={
                    "lea_id": lea_state.lea_id,
                    "page_event_count": page_size,
                    "baseline_median": baseline_events,
                    "multiplier_applied": config.volume_spike_multiplier,
                    "spike_limit": spike_limit,
                },
            )
        )

    deletion_share = (
        deletion_events / page_size if page_size > 0 else 0.0
    )
    if lea_state.recent_deletion_counts:
        baseline_deletions = max(
            1.0, float(median(lea_state.recent_deletion_counts))
        )
    else:
        baseline_deletions = 1.0
    deletion_spike_limit = (
        baseline_deletions * config.deletion_burst_multiplier
    )
    if (
        deletion_share >= config.deletion_share_threshold
        and deletion_events > deletion_spike_limit
    ):
        issues.append(
            issue(
                layer=5,
                code="THRESHOLD_DELETION_BURST",
                severity=Severity.WARNING,
                event_id=None,
                detail={
                    "lea_id": lea_state.lea_id,
                    "deletion_events": deletion_events,
                    "deletion_share": deletion_share,
                    "deletion_share_threshold": (
                        config.deletion_share_threshold
                    ),
                    "baseline_median_deletions": baseline_deletions,
                    "multiplier_applied": config.deletion_burst_multiplier,
                    "deletion_spike_limit": deletion_spike_limit,
                },
            )
        )

    if lea_state.live_student_count > 0:
        projected = (
            lea_state.live_student_count
            + student_creations
            - student_deletions
        )
        drop_fraction = (
            (lea_state.live_student_count - projected)
            / lea_state.live_student_count
        )
        if drop_fraction >= config.population_shift_threshold:
            issues.append(
                issue(
                    layer=5,
                    code="THRESHOLD_POPULATION_SHIFT",
                    severity=Severity.WARNING,
                    event_id=None,
                    detail={
                        "lea_id": lea_state.lea_id,
                        "live_student_count": lea_state.live_student_count,
                        "projected_live_student_count": projected,
                        "drop_fraction": drop_fraction,
                        "population_shift_threshold": (
                            config.population_shift_threshold
                        ),
                    },
                )
            )

    return issues


__all__ = ["DEFAULT_CONFIG", "ThresholdConfig", "check_thresholds"]
