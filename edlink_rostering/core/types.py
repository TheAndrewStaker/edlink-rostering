"""Strongly-typed identifiers for the canonical model.

NewType wrappers wrap str at zero runtime cost; the type-checker prevents
mixing a LeaId with a StudentId. This matters most at the connector boundary
where source-specific IDs and canonical IDs both look like strings without
these wrappers.

Cursor is a dataclass (not a NewType) because the EdLink Events API needs more
than an opaque string at the framework layer: the cursor table queries
staleness against observed_at, and the revert path needs to know the underlying
event_id. The value field stays opaque to callers above the connector; only
the connector parses it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import NewType

LeaId = NewType("LeaId", str)
StudentId = NewType("StudentId", str)
SchoolId = NewType("SchoolId", str)
EnrollmentId = NewType("EnrollmentId", str)
EventId = NewType("EventId", str)


@dataclass(frozen=True)
class Cursor:
    """Opaque cursor with framework-level metadata.

    value is the partner-specific cursor token (for EdLink, the event_id of
    the highest event seen). Callers above the connector treat value as
    opaque. observed_at is the framework's timestamp of when the cursor was
    last advanced, used by the cursor-lag alert query at 20 days.
    """

    value: str
    observed_at: datetime | None = None
