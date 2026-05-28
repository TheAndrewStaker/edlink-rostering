"""Shared issue type used by all five layer modules.

Lives in a private module to keep the layer modules free of circular
imports against :mod:`pipeline`, which is the public-facing entry point.
``ValidationIssue`` and ``Severity`` are re-exported from
:mod:`edlink_rostering.services.validation` for callers.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    QUARANTINE = "quarantine"


@dataclass(frozen=True)
class ValidationIssue:
    layer: int
    code: str
    severity: Severity
    detail: dict[str, Any]
    event_id: str | None = None


def issue(
    *,
    layer: int,
    code: str,
    severity: Severity,
    detail: dict[str, Any],
    event_id: str | None = None,
) -> ValidationIssue:
    """Construct an issue with named arguments. Keyword-only at the call
    site keeps issue construction self-documenting."""

    return ValidationIssue(
        layer=layer,
        code=code,
        severity=severity,
        detail=detail,
        event_id=event_id,
    )
