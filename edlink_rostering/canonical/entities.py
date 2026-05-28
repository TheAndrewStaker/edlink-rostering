"""Canonical rostering entities.

Three of the six MVP entities (Lea, Student, Enrollment). School, Term, Teacher,
Class land in week 1-2 against the real codebase. These three are enough to
demonstrate that lea_id scoping is the discriminator on every entity and that
the connector boundary works without leaking source-specific shapes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum

from edlink_rostering.core.types import EnrollmentId, LeaId, SchoolId, StudentId


class EntityType(str, Enum):
    LEA = "lea"
    STUDENT = "student"
    ENROLLMENT = "enrollment"
    # SCHOOL, TERM, TEACHER, CLASS land week 1-2


class LeaType(str, Enum):
    """How the LEA is organized. Drives some compliance and reporting differences.

    Customer-facing copy says "district" colloquially. Code uses LEA
    throughout because it covers both traditional districts and charters.
    """

    TRADITIONAL_DISTRICT = "traditional_district"
    CHARTER_LEA = "charter_lea"
    CHARTER_CMO = "charter_cmo"
    BOCES = "boces"
    STATE_AGENCY = "state_agency"


@dataclass(frozen=True)
class Lea:
    id: LeaId
    name: str
    lea_type: LeaType
    state: str  # USPS two-letter code, e.g., "CA"
    nces_lea_id: str | None = None  # for cross-reference; not required at MVP


@dataclass(frozen=True)
class Student:
    id: StudentId
    lea_id: LeaId
    given_name: str
    family_name: str
    grade: str | None = None  # OneRoster grade strings: "KG", "01", ..., "12"
    preferred_first_name: str | None = None
    primary_school_id: SchoolId | None = None
    external_ids: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Enrollment:
    id: EnrollmentId
    lea_id: LeaId
    student_id: StudentId
    class_id: str  # canonical ClassId NewType lands week 1-2
    begin_date: date
    end_date: date | None = None  # None = currently active, open interval


# Union of all canonical entity types. Each connector's payload mapping converts
# source-specific shapes into one of these. Above the connector layer, only
# CanonicalEntity values exist.
CanonicalEntity = Lea | Student | Enrollment
