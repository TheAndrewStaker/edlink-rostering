---
paths:
  - edlink_rostering/dev/**/*.py
  - scripts/seed-*.sh
  - scripts/reset-*.sh
  - api/src/edlink_rostering/infrastructure/seed/**/*.py
  - api/scripts/seed/**/*.py
  - api/scripts/reset/**/*.sh
---

# Seed data discipline

Deterministic, idempotent seeding for dev and test environments. **Every persona, every flow, every scenario has known data at known IDs.**

The current implementation lives at:

- **Seed module:** `edlink_rostering/dev/seed.py` (entry point: `seed_realistic_state`; invoked via `python -m edlink_rostering.dev.seed`).
- **Seed script:** `scripts/seed-dev.sh`.
- **Reset scripts:** `scripts/reset-*.sh` (one per testable flow).
- **IntelliJ run configs:** `.idea/runConfigurations/Seed__Dev.xml`, `Reset__*.xml`.

The `api/src/...` paths in the frontmatter are aspirational for the production codebase that lands week 1+; the POC paths are authoritative for now.

## Migration-seed update gate (mandatory)

**When an Alembic migration adds a column, table, or behavior, the seed must be updated in the same change set.** This rule auto-loads when you edit files matched by the frontmatter so the obligation surfaces at the moment the migration is written.

The gate has two parts:

1. **Initial state.** `edlink_rostering/dev/seed.py` writes rows that exercise the new schema/feature. New table → seed at least one realistic row. New column on an existing table → backfill it on existing seed rows AND add a new seed scenario that exercises the column's distinct values. New service flow → seed the prerequisites the flow consumes (LEAs, syncs, cursors, quarantine rows, dates).
2. **Test-window dates.** If the feature is gated by a date window (cursor lag, IDEA timeline, COBRA election period, ACA measurement period), seed dates that put the gate in the currently-testable state. Use `datetime.now(UTC)` plus relative offsets so the window stays valid across dev restarts; do not hardcode an absolute date and rely on the founder to bump it.

The cautionary case is benefits-administration's Sprint 13: V30 added `enrollment_mode` and three OE endpoints, the seed wasn't updated, and the entire OE flow was untestable on the dev stack until a follow-up patch landed. Don't repeat that. The seed update is part of the migration's definition of done, not a polish task.

## Definition-of-done checklist for any migration

Use this as a pre-commit self-check on any work item that includes a migration:

- [ ] V<N> migration added under `alembic/versions/`
- [ ] `docs/database/erd.md` and `docs/database/erd.mermaid` updated per `.claude/rules/alembic.md` § "ERD update gate"
- [ ] `edlink_rostering/dev/seed.py` updated to seed at least one row exercising the new schema/behavior
- [ ] If the new behavior is gated by a date window: seed dates use `datetime.now(UTC)` + relative offsets, with an UPDATE pass to refresh on every seed run (see the stale-cursor pattern in `seed.py`)
- [ ] If the new behavior mutates state on re-test: a companion `scripts/reset-<flow>.sh` script added, mirroring the `reset-demo.sh` shape
- [ ] Tests under `tests/test_dev_seed.py` cover the new seed rows for idempotency + expected shape

## Why deterministic seeds matter

1. **Reset scripts can target by ID** without database introspection
2. **End-to-end tests can assert against known data** without setup boilerplate
3. **Demos are reproducible** across machines
4. **Debugging is easier** when "student-001" is always Alice

The discipline pays for itself in development velocity.

## Deterministic UUIDs

Use UUID5 (namespace-based) for entities, or hand-assigned UUID4 with predictable patterns:

```python
# Option 1: UUID5 with stable namespaces
SEED_NS_DISTRICTS = UUID("00000001-0000-0000-0000-000000000000")
SEED_NS_STUDENTS = UUID("00000002-0000-0000-0000-000000000000")
SEED_NS_TEACHERS = UUID("00000003-0000-0000-0000-000000000000")

def seed_student_id(name: str) -> UUID:
    return uuid5(SEED_NS_STUDENTS, name)

# Option 2: hand-assigned predictable patterns
ALICE_ID = UUID("00000000-0000-0000-0002-000000000001")
BOB_ID = UUID("00000000-0000-0000-0002-000000000002")
MS_GARCIA_ID = UUID("00000000-0000-0000-0003-000000000001")
```

Either works. The point is: same input → same UUID.

## Seed module structure

```
api/src/edlink_rostering/infrastructure/seed/
├── __init__.py
├── personas.py       # named personas (Alice, Bob, Ms. Garcia, etc.)
├── districts.py      # district + school configs
├── ieps.py           # IEP scenarios (newly evaluated, mid-amendment, etc.)
├── enrollments.py    # roster scenarios
└── runner.py         # idempotent seeder entry point
```

The seeder is invoked at dev API startup (gated by environment) and via explicit CLI commands.

## Idempotent upsert

Seeds use idempotent upserts so re-running is safe:

```python
async def seed_lea_alpha(session: AsyncSession) -> Lea:
    lea = Lea(
        id=LEA_ALPHA_ID,
        name="Alpha School District",
        state=USState.OK,
        canonical_id=LEA_ALPHA_ID,
    )
    stmt = pg_insert(Lea.__table__).values(lea.to_dict())
    stmt = stmt.on_conflict_do_update(
        index_elements=["id"],
        set_={"name": stmt.excluded.name, "state": stmt.excluded.state},
    )
    await session.execute(stmt)
    return district
```

The pattern is "create or update to match seed definition." Drift gets corrected on re-run.

## Personas catalog

Personas are documented, named, and stable. Their IDs don't change.

```python
# personas.py

@dataclass
class StudentPersona:
    id: UUID
    given_name: str
    family_name: str
    birth_date: date
    grade: str
    primary_disability: DisabilityCategory | None
    scenario: str  # human description of why this persona exists

PERSONAS = {
    "alice": StudentPersona(
        id=UUID("00000000-0000-0000-0002-000000000001"),
        given_name="Alice",
        family_name="Smith",
        birth_date=date(2010, 3, 15),
        grade="09",
        primary_disability=DisabilityCategory.SpecificLearningDisability,
        scenario="9th grader with active IEP, mid-year amendment scenario",
    ),
    "bob": StudentPersona(
        id=UUID("00000000-0000-0000-0002-000000000002"),
        given_name="Bob",
        family_name="Jones",
        birth_date=date(2011, 7, 22),
        grade="08",
        primary_disability=DisabilityCategory.OtherHealthImpairment,
        scenario="8th grader, just-evaluated, awaiting initial IEP meeting",
    ),
    "charlie": StudentPersona(
        id=UUID("00000000-0000-0000-0002-000000000003"),
        given_name="Charlie",
        family_name="Davis",
        birth_date=date(2009, 11, 8),
        grade="10",
        primary_disability=None,
        scenario="10th grader, no IEP — control case for non-IEP-related queries",
    ),
    # ... more personas
}
```

When a test needs "a student about to have annual review due," it uses the persona that scenario describes. **Don't invent new students per test.** Reuse the catalog.

## Date handling: relative to today

Seeded dates are relative to a reference date so scenarios stay fresh as wall-clock advances:

```python
from datetime import date, timedelta

REFERENCE_DATE = date.today()  # set once per seed run

def days_ago(n: int) -> date:
    return REFERENCE_DATE - timedelta(days=n)

def days_from_now(n: int) -> date:
    return REFERENCE_DATE + timedelta(days=n)

ALICE_IEP_EFFECTIVE = days_ago(90)
ALICE_IEP_ANNUAL_REVIEW_DUE = days_from_now(275)
BOB_EVALUATION_CONSENT_DATE = days_ago(45)  # 15 days from 60-day deadline
```

On reset, dates re-anchor to current `REFERENCE_DATE`. **Bob is always "15 days from 60-day deadline"** regardless of when you run the seed.

For tests that need a fixed date independent of current time, pin `REFERENCE_DATE` explicitly in the test fixture.

## Reset scripts per scenario

Per-scenario reset scripts wipe just enough to re-test a flow:

```
api/scripts/reset/
├── reset-demo.sh                       # Full reset, all personas
├── reset-alice-iep-amendment.sh        # Just Alice's IEP back to pre-amendment state
├── reset-bob-evaluation.sh             # Bob back to consent-just-received state
├── reset-charlie-no-iep.sh             # Charlie back to no-IEP state
└── ...
```

Each script:

1. Has a header comment listing the tables and rows it touches
2. Uses deterministic UUIDs to target specific rows
3. Re-runs the relevant seed code to restore canonical state

```bash
#!/bin/bash
# reset-alice-iep-amendment.sh
#
# Resets Alice's IEP to pre-amendment state:
# - Removes iep_snapshot rows for alice (logical_id ALICE_IEP_LOGICAL_ID)
# - Re-seeds the original IEP and pre-amendment state
# - Preserves: district, school, alice's roster info, other personas
#
# Use when re-testing the "amend Alice's IEP" e2e flow.

python -m api.scripts.reset.alice_iep
```

The Python implementation:

```python
async def reset_alice_iep() -> None:
    """Restore Alice's IEP to the pre-amendment scenario state."""
    async with get_session() as session:
        await session.execute(
            text("DELETE FROM iep_snapshot WHERE iep_logical_id = :id"),
            {"id": str(ALICE_IEP_LOGICAL_ID)},
        )
        await seed_alice_pre_amendment_iep(session)
        await session.commit()
```

## Seeding order matters

Seeds run in dependency order: districts → schools → users → classes → enrollments → IEPs.

```python
async def seed_all(session: AsyncSession) -> None:
    """Run all seeds in dependency order. Idempotent."""
    await seed_districts(session)
    await seed_schools(session)
    await seed_teachers(session)
    await seed_students(session)
    await seed_classes(session)
    await seed_enrollments(session)
    await seed_iep_scenarios(session)
    await session.commit()
```

If you add a new entity type, place it in dependency order. **Don't shotgun add to the end** unless that's where it belongs.

## Don't seed in production

The seeder is environment-gated:

```python
async def maybe_seed_on_startup() -> None:
    env = os.environ.get("APP_ENV", "production")
    if env not in {"local", "dev", "test"}:
        return
    if os.environ.get("DISABLE_SEED") == "1":
        return
    async with get_session() as session:
        await seed_all(session)
```

Production seeds are a category error. Production data comes from real connectors and real users.

## Connector mock data

Seed data extends to connector mocks. Each connector has a fixtures directory with sample partner payloads:

```
api/tests/fixtures/
├── oneroster/
│   ├── users_active.json
│   ├── classes_2026_fall.json
│   └── ...
├── ednition/
│   ├── webhook_user_upserted.json
│   └── ...
└── edfi/
    ├── student.json
    └── ...
```

Fixtures are real partner payloads with PII scrubbed and IDs replaced with seed-deterministic ones. **The fixtures are the closest thing to a spec** for each partner.

## Multi-tenancy in seeds

Seed at least two districts. Test that operations on one don't affect the other. The test pattern from `.claude/rules/multi-tenancy.md` requires two districts; the seed data is the foundation for that test.

```python
LEA_ALPHA_ID = UUID("00000000-0000-0000-0001-000000000001")
LEA_BRAVO_ID = UUID("00000000-0000-0000-0001-000000000002")

ALICE_IN_ALPHA = StudentPersona(...)  # lea_id=LEA_ALPHA_ID
ALICE_IN_BRAVO = StudentPersona(...)  # different student, lea_id=LEA_BRAVO_ID
```

Different students in different districts. Same first/family names is OK and even useful (tests collision behavior).

## Cross-references

- `.claude/rules/multi-tenancy.md` — seed two districts for isolation tests
- `.claude/rules/alembic.md` — schema migrations are separate from data seeds
- `.claude/rules/code-quality.md` — type hints on seed personas
