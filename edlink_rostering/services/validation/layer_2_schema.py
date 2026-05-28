"""Layer 2: schema validation.

Checks that the canonical entity carried by a :class:`NormalizedEvent`
matches the ``entity_type`` claim and that the required canonical fields
are populated. The connector's mapping already enforces some of this
(dataclass instantiation rejects missing required fields), but Layer 2
exists so a bug in the connector that drops to None silently still gets
caught before snapshots are written.

Returns an iterator of :class:`ValidationIssue` (zero issues on a clean
event). Callers append the issues to the page-level report.
"""

from __future__ import annotations

from collections.abc import Iterator

from edlink_rostering.canonical.entities import (
    Enrollment,
    EntityType,
    Lea,
    Student,
)
from edlink_rostering.events.envelope import NormalizedEvent
from edlink_rostering.services.validation._issues import (
    Severity,
    ValidationIssue,
    issue,
)


def check_schema(event: NormalizedEvent) -> Iterator[ValidationIssue]:
    """Yield schema issues for one event."""

    entity = event.entity

    if event.entity_type == EntityType.STUDENT:
        if not isinstance(entity, Student):
            yield issue(
                layer=2,
                code="ENTITY_TYPE_MISMATCH",
                severity=Severity.ERROR,
                event_id=event.event_id,
                detail={
                    "expected": "Student",
                    "actual": type(entity).__name__,
                },
            )
            return
        yield from _check_student(event, entity)
    elif event.entity_type == EntityType.ENROLLMENT:
        if not isinstance(entity, Enrollment):
            yield issue(
                layer=2,
                code="ENTITY_TYPE_MISMATCH",
                severity=Severity.ERROR,
                event_id=event.event_id,
                detail={
                    "expected": "Enrollment",
                    "actual": type(entity).__name__,
                },
            )
            return
        yield from _check_enrollment(event, entity)
    elif event.entity_type == EntityType.LEA:
        if not isinstance(entity, Lea):
            yield issue(
                layer=2,
                code="ENTITY_TYPE_MISMATCH",
                severity=Severity.ERROR,
                event_id=event.event_id,
                detail={
                    "expected": "Lea",
                    "actual": type(entity).__name__,
                },
            )

    if not event.lea_id:
        yield issue(
            layer=2,
            code="MISSING_LEA_ID",
            severity=Severity.ERROR,
            event_id=event.event_id,
            detail={},
        )

    if not event.source_event_id:
        yield issue(
            layer=2,
            code="MISSING_SOURCE_EVENT_ID",
            severity=Severity.ERROR,
            event_id=event.event_id,
            detail={},
        )


def _check_student(
    event: NormalizedEvent, student: Student
) -> Iterator[ValidationIssue]:
    if not student.id:
        yield issue(
            layer=2,
            code="STUDENT_MISSING_ID",
            severity=Severity.ERROR,
            event_id=event.event_id,
            detail={},
        )
    if not student.given_name:
        yield issue(
            layer=2,
            code="STUDENT_MISSING_GIVEN_NAME",
            severity=Severity.ERROR,
            event_id=event.event_id,
            detail={"student_id": student.id},
        )
    if not student.family_name:
        yield issue(
            layer=2,
            code="STUDENT_MISSING_FAMILY_NAME",
            severity=Severity.ERROR,
            event_id=event.event_id,
            detail={"student_id": student.id},
        )
    if student.lea_id != event.lea_id:
        yield issue(
            layer=2,
            code="STUDENT_LEA_ID_MISMATCH",
            severity=Severity.ERROR,
            event_id=event.event_id,
            detail={
                "student_id": student.id,
                "entity_lea_id": student.lea_id,
                "envelope_lea_id": event.lea_id,
            },
        )


def _check_enrollment(
    event: NormalizedEvent, enrollment: Enrollment
) -> Iterator[ValidationIssue]:
    if not enrollment.id:
        yield issue(
            layer=2,
            code="ENROLLMENT_MISSING_ID",
            severity=Severity.ERROR,
            event_id=event.event_id,
            detail={},
        )
    if not enrollment.student_id:
        yield issue(
            layer=2,
            code="ENROLLMENT_MISSING_STUDENT_ID",
            severity=Severity.ERROR,
            event_id=event.event_id,
            detail={"enrollment_id": enrollment.id},
        )
    if not enrollment.class_id:
        yield issue(
            layer=2,
            code="ENROLLMENT_MISSING_CLASS_ID",
            severity=Severity.ERROR,
            event_id=event.event_id,
            detail={"enrollment_id": enrollment.id},
        )
    if enrollment.lea_id != event.lea_id:
        yield issue(
            layer=2,
            code="ENROLLMENT_LEA_ID_MISMATCH",
            severity=Severity.ERROR,
            event_id=event.event_id,
            detail={
                "enrollment_id": enrollment.id,
                "entity_lea_id": enrollment.lea_id,
                "envelope_lea_id": event.lea_id,
            },
        )
