"""Layer 5 threshold tests.

Cover the three thresholds plus the baseline-insufficient suppression
path. These are pure-function tests (no DB) since Layer 5 takes its
inputs as plain data on :class:`LEAState`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from edlink_rostering.canonical.entities import EntityType, Student
from edlink_rostering.connectors.protocol import EventPage, Layer1Result
from edlink_rostering.core.types import Cursor, EventId, LeaId, StudentId
from edlink_rostering.events.envelope import NormalizedEvent, Operation
from edlink_rostering.services.validation import LEAState
from edlink_rostering.services.validation.layer_5_thresholds import (
    ThresholdConfig,
    check_thresholds,
)


def _student_event(
    event_id: str,
    lea_id: LeaId,
    operation: Operation,
    student_id: str = "stu-x",
) -> NormalizedEvent:
    return NormalizedEvent(
        event_id=EventId(event_id),
        lea_id=lea_id,
        entity_type=EntityType.STUDENT,
        operation=operation,
        entity=Student(
            id=StudentId(student_id),
            lea_id=lea_id,
            given_name="Test",
            family_name="Student",
            grade="05",
        ),
        source_connector="edlink",
        source_event_id=event_id,
        occurred_at=datetime(2026, 5, 21, 12, 0, 0, tzinfo=UTC),
        received_at=datetime.now(UTC),
    )


def _page(events: list[NormalizedEvent], lea_id: LeaId) -> EventPage:
    return EventPage(
        events=events,
        next_cursor=Cursor(value=events[-1].event_id if events else "", observed_at=None),
        has_more=False,
        retrieved_at=datetime.now(UTC),
        layer_1_check=Layer1Result(
            ok=True,
            http_status=200,
            content_type="application/json",
            body_well_formed=True,
        ),
    )


def test_baseline_insufficient_suppresses_spike_thresholds() -> None:
    lea_id = LeaId("lea-baseline-test")
    page = _page(
        [
            _student_event(f"evt_{i:03d}", lea_id, Operation.CREATED, f"stu-{i}")
            for i in range(100)
        ],
        lea_id,
    )
    state = LEAState(
        lea_id=lea_id,
        known_student_ids=set(),
        live_student_count=10,
        recent_event_counts=(5,),  # only 1 historical job
    )
    issues = check_thresholds(page, state)
    codes = {i.code for i in issues}
    assert "THRESHOLD_PAGE_OBSERVATION" in codes
    assert "THRESHOLD_BASELINE_INSUFFICIENT" in codes
    assert "THRESHOLD_EVENT_VOLUME_SPIKE" not in codes
    assert "THRESHOLD_POPULATION_SHIFT" not in codes


def test_event_volume_spike_fires_against_baseline() -> None:
    lea_id = LeaId("lea-spike-test")
    page = _page(
        [
            _student_event(f"evt_{i:03d}", lea_id, Operation.UPDATED, f"stu-{i}")
            for i in range(50)
        ],
        lea_id,
    )
    state = LEAState(
        lea_id=lea_id,
        live_student_count=200,
        recent_event_counts=(8, 10, 12, 9, 11),  # median 10
    )
    issues = check_thresholds(page, state)
    codes = {i.code for i in issues}
    assert "THRESHOLD_EVENT_VOLUME_SPIKE" in codes


def test_event_volume_within_baseline_does_not_fire() -> None:
    lea_id = LeaId("lea-quiet-test")
    page = _page(
        [
            _student_event(f"evt_{i:03d}", lea_id, Operation.UPDATED, f"stu-{i}")
            for i in range(11)
        ],
        lea_id,
    )
    state = LEAState(
        lea_id=lea_id,
        live_student_count=200,
        recent_event_counts=(8, 10, 12, 9, 11),
    )
    issues = check_thresholds(page, state)
    codes = {i.code for i in issues}
    assert "THRESHOLD_EVENT_VOLUME_SPIKE" not in codes


def test_population_shift_fires_on_mass_deletion() -> None:
    lea_id = LeaId("lea-pop-test")
    page = _page(
        [
            _student_event(f"evt_{i:03d}", lea_id, Operation.DELETED, f"stu-{i}")
            for i in range(60)
        ],
        lea_id,
    )
    state = LEAState(
        lea_id=lea_id,
        live_student_count=200,
        recent_event_counts=(50, 60, 55, 65, 58),
        recent_deletion_counts=(2, 3, 1, 2, 4),
    )
    issues = check_thresholds(page, state)
    codes = {i.code for i in issues}
    assert "THRESHOLD_POPULATION_SHIFT" in codes
    assert "THRESHOLD_DELETION_BURST" in codes


def test_population_shift_below_threshold_does_not_fire() -> None:
    lea_id = LeaId("lea-small-pop-test")
    page = _page(
        [
            _student_event(f"evt_{i:03d}", lea_id, Operation.DELETED, f"stu-{i}")
            for i in range(10)
        ],
        lea_id,
    )
    state = LEAState(
        lea_id=lea_id,
        live_student_count=200,
        recent_event_counts=(50, 60, 55, 65, 58),
        recent_deletion_counts=(2, 3, 1, 2, 4),
    )
    issues = check_thresholds(page, state)
    codes = {i.code for i in issues}
    assert "THRESHOLD_POPULATION_SHIFT" not in codes


def test_config_overrides_spike_multiplier() -> None:
    lea_id = LeaId("lea-config-test")
    page = _page(
        [
            _student_event(f"evt_{i:03d}", lea_id, Operation.UPDATED, f"stu-{i}")
            for i in range(15)
        ],
        lea_id,
    )
    state = LEAState(
        lea_id=lea_id,
        live_student_count=200,
        recent_event_counts=(8, 10, 12, 9, 11),
    )
    issues_default = check_thresholds(page, state)
    issues_strict = check_thresholds(
        page, state, ThresholdConfig(volume_spike_multiplier=1.2)
    )
    assert "THRESHOLD_EVENT_VOLUME_SPIKE" not in {
        i.code for i in issues_default
    }
    assert "THRESHOLD_EVENT_VOLUME_SPIKE" in {
        i.code for i in issues_strict
    }


# Keep an unused-import reference so mypy does not flag the date helper
# the test module imports indirectly through canonical entities.
_ = date
