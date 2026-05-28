---
paths:
  - api/src/edlink_rostering/canonical/iep/**/*.py
  - api/src/edlink_rostering/canonical/**/temporal/**/*.py
  - alembic/versions/**/*.py
---

# Temporal model

IEPs evolve over time. Amendments, annual reviews, triennial reevaluations, transition plans, BIP additions — the same student can have a sequence of distinct IEP states across years. **The application models IEP versions as append-only temporal snapshots.**

## Why temporal snapshots

Three reasons:

1. **Legal evidence.** An IEP is a legal document. When questions arise about "what was in effect on date X," the system must produce that exact version, not a reconstruction.
2. **Compliance reporting.** State and federal reports query IEP state at specific points in time (e.g., "IEPs in effect on December 1").
3. **Amendment provenance.** Knowing what changed between versions, when, and by whom is operationally important.

A mutable "current state" model loses this. Append-only snapshots preserve it.

## The shape

Per-IEP entities are versioned. Mutations create new versions; old versions remain.

```python
class IEPSnapshot(Base):
    __tablename__ = "iep_snapshot"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    lea_id: Mapped[LeaId] = mapped_column(index=True)
    iep_logical_id: Mapped[UUID] = mapped_column(index=True)  # stable across versions
    student_id: Mapped[StudentId] = mapped_column(index=True)

    # Version metadata
    version: Mapped[int]  # monotonically increasing per logical_id
    supersedes_id: Mapped[UUID | None] = mapped_column(ForeignKey("iep_snapshot.id"))
    superseded_by_id: Mapped[UUID | None] = mapped_column(ForeignKey("iep_snapshot.id"))

    # Validity window (inclusive of effective_date; see end-date convention below)
    effective_date: Mapped[date]
    end_date: Mapped[date | None]  # null = currently in effect (open-ended)

    # Provenance
    created_at: Mapped[datetime]
    created_by: Mapped[str]  # actor identifier
    amendment_reason: Mapped[str | None]
    source_event_id: Mapped[UUID | None]  # link to event that triggered creation

    # The actual IEP state at this version (immutable once written)
    primary_disability: Mapped[str]
    annual_review_date: Mapped[date]
    triennial_reevaluation_date: Mapped[date]
    # ... all SEDM-aligned fields
```

The `iep_logical_id` is the stable identifier across versions. The `id` (primary key) is unique per snapshot.

## Effective date semantics: end-date convention

**[VERIFY against SEDM convention before locking in.]**

Tentative decision per ADR-005 (inclusive both ends for rostering, applied to IEP-side pending SEDM confirmation): `effective_date` is inclusive, `end_date` is inclusive of the last day of validity. So an IEP valid from `2025-08-15` through `2026-08-14` has `effective_date=2025-08-15`, `end_date=2026-08-14`.

When a new IEP supersedes the prior one effective `2026-08-15`, the prior is updated to `end_date=2026-08-14`.

Open IEPs (currently in effect) have `end_date=NULL`.

Rationale: matches calendar reasoning ("this IEP is effective for the 2025-2026 school year"). The exclusive-end-date convention causes off-by-one bugs in legal evidence (was the IEP in effect on August 14?).

If SEDM specifies a different convention, follow SEDM and document the divergence loudly. Cross-IEP queries to state Ed-Fi feeds must match the state's expected convention.

## Query patterns

### "What IEP was in effect for student S on date D?"

```python
def find_iep_effective_at(
    session: AsyncSession,
    student_id: StudentId,
    lea_id: LeaId,
    as_of: date,
) -> IEPSnapshot | None:
    """
    Returns the IEP snapshot in effect for `student_id` in `lea_id` on `as_of`.
    Per the inclusive-end-date convention, an IEP with effective=2025-08-15 and
    end=2026-08-14 is returned for any as_of in [2025-08-15, 2026-08-14].
    """
    stmt = (
        select(IEPSnapshot)
        .where(
            IEPSnapshot.student_id == student_id,
            IEPSnapshot.lea_id == lea_id,
            IEPSnapshot.effective_date <= as_of,
            or_(
                IEPSnapshot.end_date.is_(None),
                IEPSnapshot.end_date >= as_of,
            ),
        )
        .order_by(IEPSnapshot.version.desc())
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()
```

### "What IEPs has student S ever had?"

```python
def find_iep_history(
    session: AsyncSession,
    student_id: StudentId,
    lea_id: LeaId,
) -> list[IEPSnapshot]:
    stmt = (
        select(IEPSnapshot)
        .where(
            IEPSnapshot.student_id == student_id,
            IEPSnapshot.lea_id == lea_id,
        )
        .order_by(IEPSnapshot.effective_date.asc(), IEPSnapshot.version.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars())
```

### "What IEPs are in effect across the district today?"

For compliance reports:

```python
async def iep_in_effect_count(lea_id: LeaId, as_of: date) -> int:
    stmt = select(func.count(IEPSnapshot.id)).where(
        IEPSnapshot.lea_id == lea_id,
        IEPSnapshot.effective_date <= as_of,
        or_(IEPSnapshot.end_date.is_(None), IEPSnapshot.end_date >= as_of),
    )
    result = await session.execute(stmt)
    return result.scalar_one()
```

## Amendments: create new version, supersede old

An amendment doesn't update the existing IEP; it creates a new version that supersedes.

```python
async def amend_iep(
    iep_logical_id: UUID,
    changes: IEPAmendment,
    actor: str,
    lea_id: LeaId,
) -> IEPSnapshot:
    """Create a new IEP snapshot reflecting the amendment.

    The prior snapshot is updated only to set end_date and superseded_by_id;
    its actual IEP state is unchanged.
    """
    async with session.begin():
        # Get current version
        current = await iep_repo.get_current(iep_logical_id, lea_id)
        if not current:
            raise IEPNotFoundError()

        # Apply changes to produce new state
        new_state = current.with_changes(changes)

        # Create new snapshot
        new_snapshot = IEPSnapshot(
            id=uuid4(),
            lea_id=lea_id,
            iep_logical_id=iep_logical_id,
            student_id=current.student_id,
            version=current.version + 1,
            supersedes_id=current.id,
            superseded_by_id=None,
            effective_date=changes.effective_date,
            end_date=None,  # currently open
            created_at=datetime.now(UTC),
            created_by=actor,
            amendment_reason=changes.reason,
            source_event_id=changes.source_event_id,
            **new_state.field_dict(),
        )
        session.add(new_snapshot)

        # Update prior to point forward and close its window
        current.superseded_by_id = new_snapshot.id
        current.end_date = changes.effective_date - timedelta(days=1)

        return new_snapshot
```

## What's immutable, what's mutable

**Immutable on a snapshot once written:**

- All IEP state fields (goals, services, disability category, etc.)
- `effective_date`, `created_at`, `created_by`, `version`
- `supersedes_id` (points backward)

**Mutable on a snapshot:**

- `end_date` — set when a successor is created
- `superseded_by_id` — set when a successor is created

Nothing else changes after the initial write. **If you need to correct an error, create a corrective amendment with `amendment_reason="correction"`, don't edit the original.**

## "Currently in effect" is a query, not a column

Don't add an `is_current` or `is_active` boolean. Derive currency from the validity window:

```python
def is_currently_in_effect(snapshot: IEPSnapshot) -> bool:
    today = date.today()
    return (
        snapshot.effective_date <= today
        and (snapshot.end_date is None or snapshot.end_date >= today)
    )
```

Adding redundant booleans creates consistency bugs (the boolean and the dates drift apart).

## Services attached to IEP

Services (e.g., 30 minutes of speech therapy 5x/week) are scoped to an IEP snapshot. When the IEP is amended and a new snapshot is created, services are re-attached to the new snapshot:

```python
class IEPService(Base):
    __tablename__ = "iep_service"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    iep_snapshot_id: Mapped[UUID] = mapped_column(ForeignKey("iep_snapshot.id"))
    lea_id: Mapped[LeaId]
    service_type: Mapped[str]  # SEDM ServiceTypeDescriptor
    minutes_per_session: Mapped[int]
    sessions_per_period: Mapped[int]
    period: Mapped[str]
    location: Mapped[str]
    begin_date: Mapped[date]
    end_date: Mapped[date | None]
```

When amending, services that are unchanged are re-attached by reference; services that change get new rows. **Don't mutate service rows on the prior snapshot.**

## Goals attached to IEP

Same pattern for goals. Goals have their own progression tracking (separately temporal):

```python
class IEPGoalProgress(Base):
    __tablename__ = "iep_goal_progress"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    iep_goal_id: Mapped[UUID] = mapped_column(ForeignKey("iep_goal.id"))
    lea_id: Mapped[LeaId]
    recorded_at: Mapped[datetime]
    recorded_by: Mapped[str]
    progress_value: Mapped[str]  # qualitative or quantitative; depends on goal type
    progress_note: Mapped[str | None]
```

Progress entries are append-only within the goal's lifetime. They don't replace prior progress; they accumulate.

## Audit log integration

Every snapshot creation produces an audit log entry. The audit log captures who made the change, when, and what the amendment reason was. The audit log does NOT duplicate the IEP body (that lives in the snapshot row).

## Data retention

Snapshots are retained per the district's retention policy. **Don't delete superseded snapshots while the IEP's logical_id is still within retention.** The audit value of the history depends on completeness.

When an IEP's retention expires (typically multi-year after the student exits the district), all snapshots for that `iep_logical_id` are removed together.

## Tests

Temporal tests are a category of their own. Cover:

- Find effective IEP at a date (exact boundaries: effective_date, end_date, gaps between IEPs)
- Amendment creates new version and supersedes old
- Concurrent amendments are serialized
- Audit log captures every snapshot creation
- Open IEPs (end_date=NULL) handled correctly

```python
@pytest.mark.asyncio
async def test_inclusive_end_date_boundaries(iep_repo, student_with_iep):
    # IEP effective 2025-08-15 through 2026-08-14
    iep_aug15 = await iep_repo.find_effective_at(student_id, lea_id, date(2025, 8, 15))
    iep_aug14 = await iep_repo.find_effective_at(student_id, lea_id, date(2026, 8, 14))
    iep_aug15_next = await iep_repo.find_effective_at(student_id, lea_id, date(2026, 8, 15))

    assert iep_aug15 is not None  # first day inclusive
    assert iep_aug14 is not None  # last day inclusive
    assert iep_aug15_next is None  # day after end_date, not effective
```

## Cross-references

- SEDM — https://datastandardsunited.org/ceds-sedm
- IDEA Part B regulations — https://www.ecfr.gov/current/title-34/part-300
- `.claude/rules/compliance.md` — where deadline math sits in code
- `architecture/data-model.md` — broader canonical model
