# Canonical data model

The application's internal, source-agnostic representation of entities. Each connector translates between its source-specific payload shape and these canonical types; the translation logic lives inside the connector. A shared `SchemaMapper` protocol is not extracted unless a second concrete case demands it, per `architecture/connector-framework.md`.

The design principle: **the canonical model should outlive any single source integration.** If the application switches from EdLink to Ednition, or adds direct PowerSchool support later, the canonical model doesn't change. Only the mappers do.

## Entity inventory

### Tier 1 — Rostering (MVP scope, July deadline)

```python
@dataclass(frozen=True)
class Lea:
    id: LeaId
    name: str
    state: str  # US state code
    nces_id: str | None  # National Center for Education Statistics ID
    timezone: str
    metadata: dict  # source-specific overflow

@dataclass(frozen=True)
class School:
    id: SchoolId
    lea_id: LeaId
    name: str
    school_type: SchoolType  # ELEMENTARY | MIDDLE | HIGH | OTHER
    grade_range: tuple[Grade, Grade]
    nces_id: str | None
    metadata: dict

@dataclass(frozen=True)
class Person:
    """Base type for Student and Teacher. Identifying info."""
    id: PersonId
    given_name: str
    family_name: str
    email: str | None
    external_ids: dict[str, str]  # {"sis_id": "...", "state_id": "..."}

@dataclass(frozen=True)
class Student(Person):
    student_role: StudentRole  # STUDENT (typed for future flexibility)
    grade: Grade
    school_ids: list[SchoolId]
    date_of_birth: date | None
    demographics: Demographics | None  # see below

@dataclass(frozen=True)
class Teacher(Person):
    teacher_role: TeacherRole  # TEACHER | SPECIALIST | ADMIN
    school_ids: list[SchoolId]

@dataclass(frozen=True)
class Class:
    id: ClassId
    school_id: SchoolId
    name: str
    subject: str
    grade: Grade
    term_id: TermId
    teacher_ids: list[TeacherId]

@dataclass(frozen=True)
class Enrollment:
    id: EnrollmentId
    student_id: StudentId
    class_id: ClassId
    role: EnrollmentRole  # STUDENT | OBSERVER | ...
    start_date: date
    end_date: date | None
    status: EnrollmentStatus  # ACTIVE | INACTIVE | DROPPED

@dataclass(frozen=True)
class Term:
    id: TermId
    lea_id: LeaId
    name: str
    start_date: date
    end_date: date
```

### Tier 2 — IEP (post-MVP, but design now to avoid rework)

```python
@dataclass(frozen=True)
class IEP:
    """The IEP document for a single student."""
    id: IEPId
    student_id: StudentId
    school_id: SchoolId
    case_manager_id: TeacherId  # the special-ed teacher owning this IEP
    effective_date: date
    annual_review_date: date
    triennial_reevaluation_date: date
    primary_disability: DisabilityCategory  # IDEA 13 categories
    secondary_disabilities: list[DisabilityCategory]
    status: IEPStatus  # DRAFT | ACTIVE | EXPIRED | ARCHIVED

@dataclass(frozen=True)
class IEPGoal:
    """An annual measurable goal in an IEP. Multiple per IEP."""
    id: GoalId
    iep_id: IEPId
    goal_text: str  # the actual goal language
    domain: GoalDomain  # ACADEMIC | BEHAVIORAL | COMMUNICATION | etc.
    baseline: str
    target: str
    measurement_method: str
    review_frequency: ReviewFrequency

@dataclass(frozen=True)
class IEPGoalProgress:
    """A periodic measurement against a goal."""
    id: ProgressId
    goal_id: GoalId
    measured_at: datetime
    measured_by: TeacherId
    quantitative_value: float | None
    qualitative_notes: str | None
    progress_status: ProgressStatus  # ON_TRACK | BEHIND | ACHIEVED

@dataclass(frozen=True)
class IEPService:
    """A service the school provides under an IEP."""
    id: ServiceId
    iep_id: IEPId
    service_type: ServiceType  # SPEECH | OT | PT | SPECIALIZED_INSTRUCTION | etc.
    provider_id: TeacherId
    minutes_per_session: int
    sessions_per_period: int
    period: ServicePeriod  # WEEK | MONTH
    location: ServiceLocation
    start_date: date
    end_date: date | None
```

### Tier 3 — LMS (post-MVP)

```python
@dataclass(frozen=True)
class Assignment:
    id: AssignmentId
    class_id: ClassId
    name: str
    description: str
    due_date: datetime
    points_possible: float

@dataclass(frozen=True)
class Submission:
    id: SubmissionId
    assignment_id: AssignmentId
    student_id: StudentId
    submitted_at: datetime
    score: float | None
    feedback: str | None
```

### Tier 4 — Assessments (post-MVP)

```python
@dataclass(frozen=True)
class AssessmentResult:
    id: AssessmentResultId
    student_id: StudentId
    assessment_name: str  # "NWEA MAP Reading"
    administered_at: datetime
    rit_score: int | None  # NWEA-specific
    percentile: int | None
    raw_score: float | None
    growth_measure: float | None
```

## Common design decisions

### IDs are framework-issued, not source-issued

The framework mints its own canonical IDs for every entity. Source IDs live in `external_ids` for traceability and lookup.

Why: a student might appear in multiple sources (SIS, IEP system, LMS) with different source-side IDs. The canonical ID ties them together. Switching sources doesn't invalidate canonical IDs.

```python
StudentId = NewType("StudentId", str)  # Framework UUID
```

The mapper handles source-ID-to-canonical-ID resolution:

```python
async def resolve_student(source_id: str, source: str) -> StudentId:
    return await identity_resolver.resolve(
        source=source,
        source_id=source_id,
        entity_type=EntityType.STUDENT,
    )
```

### Immutable entities, mutation via new versions

All canonical entities are frozen dataclasses. State changes produce new versions with the same ID, not in-place mutations. Useful for audit, replay, and time-travel queries.

### Audit fields are universal but stored separately

Don't pollute domain entities with `created_at`, `created_by`, `version`, etc. Those live in an audit envelope around the entity:

```python
@dataclass(frozen=True)
class AuditedEntity[T]:
    entity: T
    version: int
    created_at: datetime
    created_by: ActorId
    updated_at: datetime
    updated_by: ActorId
    source_connector: str
```

### Demographics is a separate, gated type

Demographics data (race, ethnicity, IEP-disability category) has higher privacy requirements than baseline roster data. Separating it makes access control simpler.

```python
@dataclass(frozen=True)
class Demographics:
    race: list[Race] | None
    ethnicity: Ethnicity | None
    primary_language: str | None
    el_status: EnglishLearnerStatus | None  # English Learner
    ## Note: disability category lives on IEP, not here, because the
    ## IEP is the legal authoritative record for that data.
```

Access to `Demographics` should require a higher authorization scope than access to `Student`.

### External IDs are bidirectional lookup

Every entity carries a map of source-specific external IDs:

```python
external_ids: dict[str, str]
# e.g., {
#   "sis_powerschool": "12345",
#   "state_id": "CA987654321",
#   "clever_id": "abc-def-...",
# }
```

This supports both inbound resolution (source ID → canonical ID) and outbound write-back (canonical ID → source ID for the right partner).

### Soft delete, not hard delete

Entities are marked `deleted_at` rather than removed. Hard delete only on explicit district revocation after retention period, per FERPA.

## Mapping examples

### Clever Student → Canonical Student

```python
# Connector-internal translation method; no shared base class yet
class CleverConnector:
    def _canonical_student(self, clever_student: dict) -> Student:
        return Student(
            id=identity_resolver.resolve_sync(
                source="clever",
                source_id=clever_student["id"],
                entity_type=EntityType.STUDENT,
            ),
            given_name=clever_student["name"]["first"],
            family_name=clever_student["name"]["last"],
            email=clever_student.get("email"),
            external_ids={
                "clever_id": clever_student["id"],
                "sis_id": clever_student.get("sis_id"),
                "state_id": clever_student.get("state_id"),
            },
            student_role=StudentRole.STUDENT,
            grade=parse_grade(clever_student["grade"]),
            school_ids=[
                identity_resolver.resolve_school(s) for s in clever_student["schools"]
            ],
            date_of_birth=parse_date(clever_student.get("dob")),
            demographics=None,  # demographics fetched separately if scoped
        )
```

### Frontline IEP → Canonical IEP

```python
# Connector-internal translation method; no shared base class yet
class FrontlineConnector:
    def _canonical_iep(self, frontline_iep: dict) -> IEP:
        return IEP(
            id=identity_resolver.resolve_iep(
                source="frontline",
                source_id=frontline_iep["iep_id"],
            ),
            student_id=identity_resolver.resolve_student(
                source="frontline",
                source_id=frontline_iep["student_id"],
            ),
            ...
        )
```

The mapper is where source-specific quirks live. Everything above the mapper sees only canonical entities.

## Open questions

## Open questions

1. Does the application have an existing canonical model? If so, this design should align rather than replace.
2. What's the AI layer's expected event payload? Same as canonical entities, or a different projection?
3. How are demographics handled today re: storage and access control?
4. Is there a separate identity resolution service already, or does the framework own that?
5. Time series for IEPGoalProgress — at what granularity? Per-data-point or per-period?

The design principles for canonical entities: source-agnostic, immutable where temporal, audit-enveloped, external-ID-mapped per ADR-003.
