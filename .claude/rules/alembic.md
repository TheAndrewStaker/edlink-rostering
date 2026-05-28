---
paths:
  - alembic/versions/**/*.py
  - alembic/env.py
  - alembic.ini
---

# Alembic migration discipline

Database migrations are written by hand, reviewed carefully, applied in order, and never edited after merge. **Migrations are forever.**

## Migrations are append-only

Once a migration is merged and applied to any environment beyond the developer's machine, **it cannot be edited.** Subsequent changes are new migrations.

Even renaming the file is risky — Alembic tracks migrations by `revision` ID, but tooling and code review patterns rely on filenames. If you find yourself wanting to edit, write a new migration.

## ERD + seed update gate

After adding or altering any migration, two artifacts MUST be updated **in the same change set as the migration**:

1. **ERD docs.**
   - `docs/database/erd.md` — full schema reference with embedded Mermaid `erDiagram`, table summary, and key-constraints section. Maintain the "Last updated" line at the top with a one-paragraph note pointing at the new migration.
   - `docs/database/erd.mermaid` — standalone Mermaid file for use with Excalidraw, IntelliJ, or other external rendering tools.

   Both files MUST show every table, every column with its type, every PK/FK constraint, and every relationship line. Use database column names (`snake_case`), not Python attribute names. The mermaid block is the canonical reference for downstream tools and onboarding; a stale ERD is worse than no ERD.

2. **Dev seed.** Per `.claude/rules/seed-data.md` § "Migration-seed update gate", `edlink_rostering/dev/seed.py` must be updated to seed at least one row exercising the new schema/behavior. If the new behavior is gated by a date window, seed dates use `datetime.now(UTC)` + relative offsets with an UPDATE pass on every seed run. If the new behavior mutates state on re-test, add a companion `scripts/reset-<flow>.sh`.

This rule auto-loads when editing files matched by the frontmatter (`alembic/versions/**/*.py`, `alembic/env.py`, `alembic.ini`). If the migration adds a column, table, or constraint and either the ERD edit or the seed edit does not show up in the diff, the change set is incomplete.

## Generate, don't write blank

Use `alembic revision --autogenerate -m "<short_description>"` to start. Autogenerate detects schema drift from SQLAlchemy models. **Always review the generated migration** — autogenerate misses things (renames, data migrations, constraints with custom names).

```bash
alembic revision --autogenerate -m "add_iep_snapshot_supersedes_index"
```

## Naming

Migration messages are concise, lowercase, underscore-separated. The filename includes a timestamp prefix:

```
alembic/versions/2026_05_11_1430-abc123def456_add_iep_snapshot_supersedes_index.py
```

The message should describe the change, not its motivation. Motivation goes in the migration body's docstring.

## Migration structure

```python
"""add iep_snapshot supersedes index

Revision ID: abc123def456
Revises: 9b8d7e6f5a4c
Create Date: 2026-05-11 14:30:00.000000

Performance optimization for the "find effective IEP at date" query
which filters by lea_id + student_id + effective_date.
Identified as bottleneck in the IEP timeline check job (50ms per
student, ~10x improvement with this index).
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "abc123def456"
down_revision = "9b8d7e6f5a4c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_iep_snapshot_district_student_effective",
        "iep_snapshot",
        ["lea_id", "student_id", "effective_date"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_iep_snapshot_district_student_effective",
        table_name="iep_snapshot",
    )
```

The docstring explains *why*. The code explains *what*. Both matter.

## Downgrade discipline

Every migration has a `downgrade()` that reverses the change. **Reversible by default.** Migrations that can't be reversed (e.g., data destruction) need explicit acknowledgment in the docstring and PR description.

Don't lazily write `pass` in downgrade. Either:

1. Implement the reverse correctly
2. Document why downgrade is impossible

## Multi-tenancy: lea_id in indexes and constraints

Per `.claude/rules/multi-tenancy.md`, district scoping is enforced at the database where possible. Migrations that create student-data tables should include:

```python
def upgrade() -> None:
    op.create_table(
        "iep_snapshot",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("lea_id", sa.String(64), nullable=False),
        sa.Column("student_id", postgresql.UUID(as_uuid=True), nullable=False),
        # ... other columns ...
    )
    op.create_index("ix_iep_snapshot_district", "iep_snapshot", ["lea_id"])
    op.create_index(
        "ix_iep_snapshot_district_student",
        "iep_snapshot",
        ["lea_id", "student_id"],
    )
```

LEA-scoped composite indexes go first. Single-column non-LEA indexes are rare (and need justification).

## Data migrations

When a schema change requires data transformation:

1. **Make the schema change in one migration.**
2. **Run the data migration in a separate migration.**
3. **Make the data validation / cleanup in a third migration.**

This separation allows partial rollback and makes data migrations independently auditable.

```python
# 2026_05_11_1430-aaa111-add_canonical_id_to_student.py — schema only
def upgrade() -> None:
    op.add_column("student", sa.Column("canonical_id", postgresql.UUID(as_uuid=True), nullable=True))

# 2026_05_11_1432-bbb222-backfill_canonical_id.py — data only
def upgrade() -> None:
    connection = op.get_bind()
    students = connection.execute(text("SELECT id FROM student WHERE canonical_id IS NULL")).fetchall()
    for row in students:
        canonical = uuid4()
        connection.execute(
            text("UPDATE student SET canonical_id = :c WHERE id = :id"),
            {"c": str(canonical), "id": str(row.id)},
        )

# 2026_05_11_1434-ccc333-enforce_canonical_id_not_null.py — constraint
def upgrade() -> None:
    op.alter_column("student", "canonical_id", nullable=False)
```

The three-migration pattern reduces blast radius if data migration fails mid-run.

## Long-running data migrations: out-of-band

Data migrations on millions of rows shouldn't block deployment. Instead:

1. Add the new column nullable
2. Deploy code that writes new values to both old and new
3. Run an out-of-band backfill (one-time job, idempotent)
4. Deploy code that reads from new column
5. Drop old column in a follow-up migration

The migration in step 1 is small; backfill in step 3 is operational, not a migration; subsequent migrations are small.

## Foreign keys

Always name foreign keys explicitly:

```python
op.create_foreign_key(
    "fk_iep_snapshot_supersedes_id",
    "iep_snapshot",
    "iep_snapshot",
    ["supersedes_id"],
    ["id"],
)
```

Named constraints are essential for `downgrade()` (you need the name to drop) and for cross-database portability.

## Per-multi-tenancy composite FKs (advanced)

For elevated defense-in-depth, foreign keys can include `lea_id`:

```python
# This requires both the parent and child to have (lea_id, primary_key)
# as a unique constraint.
op.create_unique_constraint("uq_student_lea_id", "student", ["lea_id", "id"])
op.create_foreign_key(
    "fk_enrollment_student",
    "enrollment",
    "student",
    ["lea_id", "student_id"],
    ["lea_id", "id"],
)
```

This makes a cross-district reference a database integrity error rather than a silent leak. Use for high-sensitivity tables.

## Indexes for performance

Add indexes for the actual query patterns. **Don't add indexes speculatively.** Watch the query log; add indexes when a slow query is identified.

When adding an index on a large table in production, use `CREATE INDEX CONCURRENTLY` (Postgres-specific):

```python
def upgrade() -> None:
    # Concurrent index creation requires running outside a transaction
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_iep_snapshot_effective_date",
            "iep_snapshot",
            ["effective_date"],
            postgresql_concurrently=True,
        )
```

## Migration ordering across branches

If two branches add migrations in parallel, Alembic's `down_revision` linkage will conflict. Resolve by:

1. Merging one branch first
2. Rebasing the second branch's migrations to chain from the merged head

`alembic merge` exists for merging branches but creates an extra revision; rebasing migration files is usually cleaner.

## CI gates

CI runs:

1. `alembic upgrade head` against a fresh database (verifies migrations apply cleanly)
2. `alembic downgrade -1` and re-upgrade (verifies reversibility)
3. Schema diff between SQLAlchemy models and migrated database (catches missing migrations)

Failing any of these blocks merge.

## Production deployment

Migrations run before application code that depends on them. Standard deployment pattern:

1. Run `alembic upgrade head` against production
2. Wait for completion (with timeout monitoring)
3. Deploy new application code
4. Roll back application code if anything fails

For destructive migrations (drop column, drop table), use a two-phase deployment:

1. Deploy application code that doesn't read/write the column
2. Run the migration
3. Cleanup

## Schema drift detection

A scheduled job compares the production database schema against the migration head. Drift indicates someone modified the production schema outside of migrations — which should never happen. Alerts fire when drift is detected.

## Per-environment seed data

Seed data is NOT in migrations. Use the seed-data pattern per `.claude/rules/seed-data.md` instead.

Migrations are for schema. Seeds are for data. Conflating them creates portability problems.

## Migration template

Save this skeleton when starting any migration:

```python
"""<short_message>

Revision ID: <generated>
Revises: <previous>
Create Date: <generated>

<paragraph explaining motivation. why this change. what query or
feature requires it. any caveats or rollout considerations.>
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "<generated>"
down_revision = "<previous>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """<one-line description of forward change>"""
    pass


def downgrade() -> None:
    """<one-line description of reverse change>"""
    pass
```

## Cross-references

- `.claude/rules/multi-tenancy.md` — lea_id discipline in schema
- `.claude/rules/seed-data.md` — seed data lives outside migrations
- `.claude/rules/security.md` — encryption-at-rest, access controls (set at infra level, not migrations)
- `architecture/data-model.md` — canonical entity definitions
- `docs/database/erd.md` — ER diagram, table summary, key constraints (kept in lockstep with migrations per the ERD update gate above)
- `docs/database/erd.mermaid` — standalone mermaid source for external rendering
