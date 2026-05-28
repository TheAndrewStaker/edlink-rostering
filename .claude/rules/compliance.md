---
paths:
  - api/src/edlink_rostering/compliance/**/*.py
  - api/src/edlink_rostering/canonical/iep/**/*.py
  - api/src/edlink_rostering/infrastructure/scheduling/**/*.py
---

# Compliance math discipline

K-12 EdTech compliance math (IDEA timelines, FERPA disclosure logging, COPPA data minimization, state-specific deadlines) **lives in dedicated domain services, not in controllers, not in models.** Reference: `docs/compliance/GROUNDING_SOURCES.md` for the authoritative URL index, `docs/compliance/state-privacy-laws.md` for per-state parameter tables.

## Why isolate

Three reasons:

1. **Verifiability.** Compliance code is the regulatory surface. Regulators, auditors, and counsel can read it once and verify it without spelunking through the application.
2. **Parameterization.** State variations, federal rule updates, and per-district custom policies all flow through one place.
3. **Fail-loud safety.** Compliance failures should be visible. Hiding them in route handlers buries them.

## Where compliance code lives

```
api/src/edlink_rostering/compliance/
├── idea_timelines.py         # 60-day evaluation, annual review, etc.
├── idea_categories.py        # 13 disability categories + state extensions
├── idea_procedural.py        # prior written notice, consent tracking
├── idea_discipline.py        # manifestation determination triggers
├── ferpa_disclosure.py       # disclosure logging, exceptions
├── ferpa_retention.py        # retention policies per district
├── coppa_minimization.py     # data scope enforcement
├── state/                    # per-state extensions
│   ├── california.py
│   ├── texas.py
│   ├── illinois.py
│   └── ...
└── audit/                    # audit log integration
    └── log_decorator.py
```

Every file in `compliance/` cites the regulatory basis inline (see "Citation discipline" below).

## Parameterization is required

Hardcoded deadlines are a bug. Use parameterized functions:

```python
# Good
def initial_evaluation_deadline(
    consent_received: date,
    state: USState,
    school_calendar: SchoolCalendar | None = None,
) -> date:
    """
    Per IDEA 34 CFR § 300.301(c)(1), evaluation must be completed within 60 days
    of parental consent, unless the state has established a different timeframe.

    State variations:
    - Texas: 45 school days (stricter)
    - California: 60 calendar days, exclusive of school breaks of 5+ days
    - Florida: 60 school days
    - Default: 60 calendar days

    Verified 2026-05-12 against:
    https://www.ecfr.gov/current/title-34/part-300/section-300.301
    """
    policy = STATE_POLICIES.get(state, FEDERAL_DEFAULT)
    return policy.compute_deadline(consent_received, school_calendar)

# Bad
def initial_evaluation_deadline(consent_received: date) -> date:
    return consent_received + timedelta(days=60)
```

The federal default policy and each state policy implement a common interface. Adding a state means adding a policy class, not modifying the function.

## State extensions live in `state/`

```python
# compliance/state/texas.py
from datetime import date, timedelta

class TexasEvaluationPolicy:
    """Texas evaluation timeline policy.

    Per 19 TAC § 89.1011, evaluation must be completed within 45 school days
    of receipt of written, signed parental consent.

    Verified 2026-05-11 against:
    https://tea.texas.gov/academics/special-student-populations/special-education
    """

    DAYS = 45
    DAY_KIND = "school"

    def compute_deadline(
        self,
        consent_received: date,
        school_calendar: SchoolCalendar | None,
    ) -> date:
        if school_calendar is None:
            raise CompliancePolicyError(
                "Texas evaluation policy requires school calendar to compute school-day deadline"
            )
        return school_calendar.add_school_days(consent_received, self.DAYS)
```

The state policy is the single source of truth for that state's rule. **If state rule changes, update the policy file. Search-and-replace across the codebase shouldn't be necessary.**

## Calendar days vs school days are explicit

The DAYS unit is part of the API. Don't let "60" leak around without context.

```python
class IDEATimeline:
    days: int
    day_kind: Literal["calendar", "school"]

    def compute_deadline(self, start: date, school_calendar: SchoolCalendar | None) -> date:
        if self.day_kind == "calendar":
            return start + timedelta(days=self.days)
        if school_calendar is None:
            raise CompliancePolicyError(...)
        return school_calendar.add_school_days(start, self.days)
```

Mixing calendar days and school days is a top-three category of bugs in K-12 compliance code.

## Citation discipline

Every compliance function has the regulatory basis cited in its docstring. **Citation includes:**

1. The statute or regulation reference (e.g., 34 CFR § 300.301(c)(1))
2. The plain-language meaning
3. The authoritative URL
4. The date the citation was verified

```python
def annual_review_deadline(iep_effective_date: date) -> date:
    """
    Per IDEA 34 CFR § 300.324(b)(1), the IEP team must review the child's
    IEP at least annually to determine whether the annual goals are being
    achieved.

    Verified 2026-05-12 against:
    https://www.ecfr.gov/current/title-34/part-300/section-300.324
    """
    return iep_effective_date + timedelta(days=365)  # see TODO below
```

When regulations change (e.g., reauthorization, state amendment), the verification date is the trigger to revisit.

## Fail loud on missed deadlines

When a deadline approaches without the required next event:

1. **7 days before:** notice-level alert to case manager
2. **1 day before:** warning-level alert to case manager and special-ed director
3. **Past deadline:** failure event published, prominent UI indication, district-level alert, audit log entry

```python
async def check_evaluation_deadlines(lea_id: LeaId) -> list[DeadlineAlert]:
    pending = await procedural_log.find_pending_evaluations(lea_id)
    alerts = []
    for case in pending:
        deadline = initial_evaluation_deadline(case.consent_received, district.state)
        days_remaining = (deadline - date.today()).days

        if days_remaining < 0:
            alerts.append(DeadlineAlert.failed(case, deadline, days_remaining))
            await event_publisher.publish("compliance.idea.evaluation_deadline_passed", ...)
        elif days_remaining <= 1:
            alerts.append(DeadlineAlert.critical(case, deadline, days_remaining))
        elif days_remaining <= 7:
            alerts.append(DeadlineAlert.warning(case, deadline, days_remaining))

    return alerts
```

**Failure events publish to the event bus.** Downstream systems (notification, monitoring) react. Don't silently degrade.

## Use db-scheduler / equivalent for time-triggered events

Annual review reminders, triennial reevaluation tracking, and similar date-based events are scheduled, not polled.

```python
# When an IEP is finalized, schedule the next review notice
await scheduler.schedule(
    job_type="iep.annual_review_due",
    run_at=iep.annual_review_date - timedelta(days=30),
    payload={"iep_id": iep.id, "lea_id": iep.lea_id},
)
```

The scheduler fires the job; the handler publishes the notification event. This decouples timing from polling and survives application restarts.

For Python: `apscheduler` is one common choice; `arq` and `dramatiq` have scheduling support; for stronger ordering and durability, dedicated job runners like `procrastinate` (Postgres-backed) or `db-scheduler` ports work.

## Audit logging for compliance operations

Every compliance event is audit-logged:

```python
@audit_log(operation="idea_evaluation_deadline_computed")
def compute_evaluation_deadline(consent_received: date, state: USState, ...) -> date:
    ...
```

Audit captures: who triggered the computation (could be system), what inputs were used, what deadline resulted. The audit log is the regulator's evidence that the application computed the deadlines correctly.

Audit log for deadline alerts captures alert generation AND any user dismissal:

```python
await audit_log.record(
    operation="idea_deadline_alert_fired",
    resource_type="iep_evaluation",
    resource_id=case.id,
    lea_id=lea_id,
    details={"deadline": deadline.isoformat(), "severity": "warning", "days_remaining": 7},
)
```

## Indicator 11 / 13 metrics

IDEA Part B Indicators 11 and 13 (timeline compliance and transition planning) are tracked at the state level. the application's compliance services should:

1. Expose per-district pass/fail counts for state Indicator 11 (initial evaluation timeline)
2. Expose per-district transition planning coverage for Indicator 13
3. Make these queryable for district reports

```python
async def indicator_11_metrics(lea_id: LeaId, school_year: int) -> Indicator11Metrics:
    """Returns initial evaluation timeline compliance metrics for the school year.

    Per OSEP's IDEA Part B Indicator 11, states report on percent of children
    evaluated within the timeframe established by the state. Districts feed
    into state reports.

    Verified 2026-05-11 against:
    https://sites.ed.gov/idea/data/
    """
    ...
```

## FERPA disclosure logging

Per FERPA 34 CFR § 99.32, education agencies must maintain records of disclosures. The application, operating as a school official, contributes to district disclosure records.

```python
class DisclosureLog:
    """FERPA-mandated disclosure record per 34 CFR § 99.32.

    Records disclosures of personally identifiable information from
    education records that fall outside the school-official exception
    (e.g., disclosures to other schools when student transfers,
    disclosures to state/federal authorities).

    Verified 2026-05-11 against:
    https://www.ecfr.gov/current/title-34/subtitle-A/part-99/subpart-D/section-99.32
    """

    async def record(
        self,
        student_id: StudentId,
        lea_id: LeaId,
        disclosed_to: str,
        purpose: str,
        legal_basis: DisclosureBasis,
        records_disclosed: list[str],
        date: datetime,
    ) -> DisclosureRecord:
        ...
```

Disclosures within the school-official exception (the application accessing data on behalf of the LEA) are tracked in the audit log, not the disclosure log. **The two are distinct** — confusing them is a category error.

### Audit log vs disclosure log: decision rule

The fast rule, for code review and implementation:

| Operation | Audit log | Disclosure log |
|---|---|---|
| Application staff or system reads/writes a student record on behalf of the LEA (school-official exception) | yes | no |
| Teacher in the LEA views a student's IEP through the application's UI | yes | no |
| Case manager amends an IEP | yes | no |
| AI feature generates a progress summary | yes | no |
| Student record transferred to a new LEA when the student moves | yes | **yes** |
| Record disclosed to a state or federal authority (e.g., OSEP audit) | yes | **yes** |
| Parental disclosure to an outside party at parent's directive | yes | **yes** |
| Subpoena response | yes | **yes** |
| Researcher access under a § 99.31(a)(6) studies exception | yes | **yes** |
| Health/safety emergency disclosure | yes | **yes** |

Heuristic: **if the access is the application acting as the LEA's school official, audit only. If the access is a disclosure to a party outside the school-official scope, both audit and disclosure log.**

The disclosure log is a FERPA-mandated artifact per 34 CFR § 99.32; the audit log is operational evidence for SOC 2, security review, and incident response. Different retention, different access controls, different downstream consumers. Code that writes one should explicitly decide whether to write the other.

## Anti-patterns

### Anti-pattern: deadline computation inline in handlers

```python
# Bad — deadline math in the handler
@router.get("/students/{student_id}/upcoming-deadlines")
async def get_upcoming_deadlines(student_id: StudentId, ...):
    iep = await iep_repo.get(student_id)
    annual_review = iep.effective_date + timedelta(days=365)  # NO
    return {"annual_review": annual_review}

# Good — handler calls the compliance service
@router.get("/students/{student_id}/upcoming-deadlines")
async def get_upcoming_deadlines(
    student_id: StudentId,
    compliance: IDEACompliance = Depends(get_idea_compliance),
):
    deadlines = await compliance.upcoming_deadlines(student_id)
    return deadlines
```

### Anti-pattern: silently catching compliance errors

```python
# Bad — failure swallowed
try:
    deadline = compliance.initial_evaluation_deadline(case)
except Exception:
    deadline = None  # NO — compliance failures are loud

# Good — failure visible
try:
    deadline = compliance.initial_evaluation_deadline(case)
except CompliancePolicyError as e:
    log.error("compliance_policy_error", case_id=case.id, error=str(e))
    raise  # let the error surface; don't quietly return None
```

### Anti-pattern: business logic in scheduler handlers

The scheduled job's handler publishes an event. Business logic lives in event handlers, not in the scheduler handler. This keeps compliance triggers small and testable.

### Anti-pattern: deadline policies in code that's not in `compliance/`

If a deadline policy ends up in `services/` or `core/`, that's a sign it should be moved to `compliance/`. The location IS part of the discipline.

## Tests

Compliance tests are heavyweight. Cover:

- Each state policy with edge cases (consent received on a Friday, school breaks, holidays, year-end)
- Federal default policy
- Indicator 11/13 metric computation
- Deadline alerts at boundary days (8, 7, 6, 2, 1, 0, -1)
- Failure events published when deadlines pass
- Audit log entries produced for every compliance computation

```python
@pytest.mark.parametrize("state,expected_days,day_kind", [
    (USState.CA, 60, "calendar"),
    (USState.TX, 45, "school"),
    (USState.FL, 60, "school"),
    (USState.FEDERAL, 60, "calendar"),
])
def test_state_evaluation_deadlines(state, expected_days, day_kind):
    ...
```

## When the regulation changes

The shelf-life check in `docs/compliance/GROUNDING_SOURCES.md` is the trigger. When a regulation changes:

1. Update the per-framework reference doc
2. Update the affected compliance service with the new citation
3. Refresh the verification date
4. Run the test suite (compliance tests should still pass; if they don't, the change is breaking)
5. If breaking, coordinate with product and customers (deadline grace periods, etc.)
6. Publish an ADR documenting the change

