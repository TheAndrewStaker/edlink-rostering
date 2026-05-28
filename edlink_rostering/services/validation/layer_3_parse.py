"""Layer 3: parse-time validation.

Date parsing, role validity, and identifier-format checks for events that
cleared Layer 2 (schema). Some of these are enforced at the connector
boundary (the EdLink mapper raises if a required date is missing). Layer 3
is the defense-in-depth check that catches connector regressions and
non-canonical date strings that snuck through.

Lightweight by design in session 1. The OneRoster grade strings ("KG",
"01", ..., "12", "PS", "PK", "TK") are the most useful canonical check
because grade is what downstream AI features key on.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

from edlink_rostering.canonical.entities import Enrollment, EntityType, Student
from edlink_rostering.events.envelope import NormalizedEvent
from edlink_rostering.services.validation._issues import (
    Severity,
    ValidationIssue,
    issue,
)


# OneRoster 1.2 enumeration for grade strings. Extra values found in real
# rostering feeds: "AD" (adult education), "OT" (other), "UG" (ungraded).
# Accept the union so the POC does not reject valid edge cases. Per
# https://www.imsglobal.org/spec/oneroster/v1p2#_grades_enumeration.
_ONEROSTER_GRADES = {
    "IT", "PR", "PK", "TK", "KG",
    "01", "02", "03", "04", "05", "06",
    "07", "08", "09", "10", "11", "12",
    "13", "PS", "UG", "Other",
}


def check_parse(event: NormalizedEvent) -> Iterator[ValidationIssue]:
    """Yield Layer 3 issues for one event."""

    if event.entity_type == EntityType.STUDENT and isinstance(
        event.entity, Student
    ):
        yield from _check_student_parse(event, event.entity)
    elif event.entity_type == EntityType.ENROLLMENT and isinstance(
        event.entity, Enrollment
    ):
        yield from _check_enrollment_parse(event, event.entity)


def _check_student_parse(
    event: NormalizedEvent, student: Student
) -> Iterator[ValidationIssue]:
    if student.grade is not None and student.grade not in _ONEROSTER_GRADES:
        yield issue(
            layer=3,
            code="STUDENT_GRADE_NOT_ONEROSTER",
            severity=Severity.WARNING,
            event_id=event.event_id,
            detail={
                "student_id": student.id,
                "grade": student.grade,
                "expected": "OneRoster 1.2 grade enum",
            },
        )


def _check_enrollment_parse(
    event: NormalizedEvent, enrollment: Enrollment
) -> Iterator[ValidationIssue]:
    if not isinstance(enrollment.begin_date, date):
        yield issue(
            layer=3,
            code="ENROLLMENT_BEGIN_DATE_NOT_DATE",
            severity=Severity.ERROR,
            event_id=event.event_id,
            detail={"enrollment_id": enrollment.id},
        )
        return
    if enrollment.end_date is not None and not isinstance(
        enrollment.end_date, date
    ):
        yield issue(
            layer=3,
            code="ENROLLMENT_END_DATE_NOT_DATE",
            severity=Severity.ERROR,
            event_id=event.event_id,
            detail={"enrollment_id": enrollment.id},
        )
        return
    if (
        enrollment.end_date is not None
        and enrollment.end_date < enrollment.begin_date
    ):
        yield issue(
            layer=3,
            code="ENROLLMENT_END_BEFORE_BEGIN",
            severity=Severity.ERROR,
            event_id=event.event_id,
            detail={
                "enrollment_id": enrollment.id,
                "begin_date": enrollment.begin_date.isoformat(),
                "end_date": enrollment.end_date.isoformat(),
            },
        )
