"""Layer 4: referential validation.

Enrollments must reference students that exist somewhere the LEA can see:

1. Already in canonical (the student was created by an earlier sync), or
2. Created earlier in this batch (the enrollment depends on a person.created
   event that comes ahead of it in the same page).

Layer 4 is the only layer that routes events rather than failing them: an
orphan enrollment is sent to the ``quarantine`` table and the rest of the
batch still commits. The operator can later release the quarantined entry
once the missing student appears, or reject it.

Walking-set discipline: students that are *deleted* in this batch are kept
in the known set so enrollments can still reference them (the canonical
deletion is soft; the row still exists with deleted_at set, and the
enrollment carries its own snapshot history).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from edlink_rostering.canonical.entities import EntityType
from edlink_rostering.core.types import StudentId
from edlink_rostering.events.envelope import NormalizedEvent
from edlink_rostering.services.validation._issues import (
    Severity,
    ValidationIssue,
    issue,
)

if TYPE_CHECKING:
    from edlink_rostering.services.validation.pipeline import LEAState


def check_referential(
    events: list[NormalizedEvent],
    lea_state: "LEAState",
) -> tuple[list[ValidationIssue], set[str]]:
    """Return Layer 4 issues plus the set of event_ids to quarantine.

    Events are processed in their page order so a person.created that
    appears before an enrollment.created in the same page resolves
    correctly.
    """

    issues: list[ValidationIssue] = []
    quarantine_event_ids: set[str] = set()
    known_students: set[StudentId] = set(lea_state.known_student_ids)

    for event in events:
        if event.entity_type == EntityType.STUDENT:
            student_id = StudentId(getattr(event.entity, "id"))
            known_students.add(student_id)
        elif event.entity_type == EntityType.ENROLLMENT:
            ref_student_id = StudentId(getattr(event.entity, "student_id"))
            if ref_student_id not in known_students:
                issues.append(
                    issue(
                        layer=4,
                        code="ENROLLMENT_ORPHAN_STUDENT",
                        severity=Severity.QUARANTINE,
                        event_id=event.event_id,
                        detail={
                            "enrollment_id": getattr(event.entity, "id"),
                            "student_id": ref_student_id,
                            "lea_id": event.lea_id,
                        },
                    )
                )
                quarantine_event_ids.add(event.event_id)

    return issues, quarantine_event_ids
