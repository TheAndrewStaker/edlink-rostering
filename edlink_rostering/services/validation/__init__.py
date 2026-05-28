"""Five-layer validation pipeline for incoming partner events.

The layers are derived from the OneRoster 1.2 Implementation Guide and the
Ed-Fi Data Validation Architecture per
``docs/design/edlink-oneroster-rostering.md``:

1. **Layer 1: Response integrity.** HTTP status, content-type, body
   well-formed. Lives at the connector boundary because it is a check on
   the response object, not on the events. The sync worker reads
   :class:`Layer1Result` off :class:`EventPage` and surfaces it through
   this package's :class:`ValidationReport`.

2. **Layer 2: Schema.** The :class:`NormalizedEvent` carries an entity of
   the type its ``entity_type`` claims, required canonical fields are
   populated, and IDs are non-empty. Catches connector bugs where mapping
   silently drops a field.

3. **Layer 3: Parse-time.** Dates parse, role types are within the
   supported enum, identifiers conform to OneRoster format expectations.
   Some of this runs at the connector boundary (date parsing raises
   there); this layer adds the post-normalization checks that the
   connector does not enforce.

4. **Layer 4: Referential.** Enrollments reference students that exist in
   canonical or earlier in the batch. Layer 4 violations route the
   offending event to ``quarantine`` rather than failing the batch, so
   the rest of the page still commits.

5. **Layer 5: Business-rule thresholds.** Population-shift detection,
   change-event volume alarms. Session 1 ships this as a stub that always
   passes; the threshold computation lands in session 2.

Public surface: :func:`run_pipeline` takes an :class:`EventPage` plus an
LEA-scoped view of the canonical state (the existing student IDs in
canonical, used by Layer 4) and returns a :class:`ValidationReport`.
"""

from edlink_rostering.services.validation._issues import Severity, ValidationIssue
from edlink_rostering.services.validation.pipeline import (
    LEAState,
    ValidationReport,
    run_pipeline,
)

__all__ = [
    "LEAState",
    "Severity",
    "ValidationIssue",
    "ValidationReport",
    "run_pipeline",
]
