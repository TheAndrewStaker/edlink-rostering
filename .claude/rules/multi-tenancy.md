---
paths:
  - edlink_rostering/**/*.py
  - alembic/versions/**/*.py
  - web/src/api/**/*.{ts,tsx}
---

# Multi-tenancy

The application is multi-tenant by district from day one. **`lea_id` is on every query, every mutation, every cache key, every log line.** Forgetting it is the single fastest path to a cross-district data leak.

## The non-negotiable rule

**Every entity in the database that holds student data carries `lea_id` as a column.** Every query that reads or writes student data filters by `lea_id`. Every cache entry is keyed by `(lea_id, ...)`. Every audit log entry includes `lea_id`.

## Source of truth: JWT claim

`lea_id` comes from the authenticated request's JWT, not from URL parameters or request bodies. **Never trust a client-provided `lea_id`.**

```python
# Good — lea_id from JWT
@router.get("/students/{student_id}")
async def get_student(
    student_id: StudentId,
    auth: AuthContext = Depends(get_auth_context),
    service: StudentService = Depends(get_student_service),
) -> StudentResponse:
    student = await service.get(student_id, lea_id=auth.lea_id)
    return StudentResponse.from_domain(student)

# Bad — lea_id from URL
@router.get("/districts/{lea_id}/students/{student_id}")
async def get_student(lea_id: LeaId, student_id: StudentId, ...):
    # Even with this URL shape, lea_id MUST be verified against the
    # authenticated JWT's lea_id. Otherwise URL traversal is the
    # bug class.
    if lea_id != auth.lea_id:
        raise PermissionDenied()
    ...
```

URL-scoped district paths are acceptable (and sometimes useful) but the `lea_id` from the URL **must match** the JWT claim. Always verify.

## Repository enforcement

LEA scoping is enforced at the repository layer, not at the service or route layer. **Repositories take `lea_id` as a parameter; queries always include the filter.**

```python
class StudentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, student_id: StudentId, lea_id: LeaId) -> Student | None:
        result = await self.session.execute(
            select(Student).where(
                Student.id == student_id,
                Student.lea_id == lea_id,  # mandatory
            )
        )
        return result.scalar_one_or_none()

    async def list(self, lea_id: LeaId, limit: int, offset: int) -> list[Student]:
        result = await self.session.execute(
            select(Student)
            .where(Student.lea_id == lea_id)
            .order_by(Student.last_modified.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars())
```

**Code review checklist:** every new repository method takes `lea_id`. If it doesn't, justify in the PR description why it's safe (e.g., "queries platform-level table without district scope").

## Cross-district operations are explicit

Platform-admin operations that span districts exist (e.g., "list all districts," "global health check"). These have a separate code path with an explicit `is_platform_admin` permission check.

```python
@router.get("/platform/districts")
async def list_districts(
    auth: AuthContext = Depends(get_auth_context),
    service: DistrictService = Depends(get_district_service),
) -> list[DistrictResponse]:
    if not auth.is_platform_admin:
        raise PermissionDenied("platform admin required")
    districts = await service.list_all()
    return [DistrictResponse.from_domain(d) for d in districts]
```

Platform-admin paths are rare and audit-logged heavily. **Never make a regular endpoint platform-admin-mode-toggleable.** If it touches multiple districts, it lives on a separate route.

## Cache keys include lea_id

```python
# Good
@cache(ttl=300)
async def get_roster(lea_id: LeaId, school_id: SchoolId | None = None) -> Roster:
    ...

cache_key = f"roster:{lea_id}:{school_id or 'all'}"

# Bad
cache_key = f"roster:{school_id}"  # collision across districts
```

Same for Redis, in-process caches, lru_cache, anywhere. If two districts ask the same question, they get different answers — the cache key must reflect that.

## Connector instances are district-scoped

Each district has its own connector instances. The connector framework holds tokens, configs, and webhook secrets keyed by `(partner, lea_id)`. **Never reuse a connector instance across districts** — that's how token-leak bugs happen.

See `architecture/connector-framework.md` for the full registry pattern.

## Logging

Every log line includes `lea_id` as a structured field. Never log PII (per `.claude/rules/security.md`), but always log the district context:

```python
log = log.bind(lea_id=lea_id)
log.info("roster_sync_started", connector="ednition", since=cursor)
```

The `bind` pattern from structlog lets you scope lea_id once per request and have it on every subsequent log call.

## Audit log

Every audit log entry includes `lea_id`. Reviewing the audit log for a specific district must be trivial (typically a database index on `lea_id` is enough).

```python
await audit_log.record(
    actor=auth.user_id,
    lea_id=auth.lea_id,
    operation="iep_read",
    resource_type="iep",
    resource_id=iep.id,
    outcome="success",
)
```

The audit log table itself has `lea_id` indexed because regulators or districts may request a district-scoped audit on demand.

## Tests

Multi-tenancy tests are first-class. Every endpoint that touches student data has a test that:

1. Creates two districts
2. Creates a student under district A
3. Attempts the operation as a district B user
4. Asserts 404 or 403 (never 200 with leaked data)

This is the "tenant isolation" test pattern. Don't ship an endpoint without it.

```python
@pytest.mark.asyncio
async def test_get_student_returns_404_for_other_district(client, seed_two_districts):
    district_a, district_b, student_in_a = seed_two_districts
    response = await client.get(
        f"/students/{student_in_a.id}",
        headers={"Authorization": f"Bearer {token_for(district_b)}"},
    )
    assert response.status_code == 404
```

## Database constraints

Where feasible, enforce district scoping with database-level checks:

- Composite indexes that include `lea_id` first
- Foreign keys that include `lea_id` (so a `lea_id` mismatch produces an integrity error at the database, not just at the application)
- Row-level security (Postgres RLS) for defense in depth — optional but worth considering for high-sensitivity tables

```sql
-- Composite FK ensures student.lea_id matches enrollment.lea_id
ALTER TABLE enrollment ADD CONSTRAINT fk_enrollment_student
  FOREIGN KEY (lea_id, student_id)
  REFERENCES student(lea_id, id);
```

This makes a cross-district reference a database integrity error rather than a silent leak.

## Connector data ingest preserves lea_id

When ingesting data from a connector (Ednition, EdLink, OneRoster source, etc.), tag every record with `lea_id` at the point of ingest. **The district context comes from the connector instance, not from the data.**

```python
async def ingest_users(connector: Connector, lea_id: LeaId, users: list[ExternalUser]) -> None:
    for external_user in users:
        student = canonical_from_external(external_user, lea_id=lea_id)
        await repository.upsert(student)
```

If `lea_id` isn't determinable from connector context, that's a bug in the connector configuration. Fix it at the boundary, not by guessing in domain code.

## Frontend

Frontend code uses an authenticated API client that doesn't expose `lea_id` choice to UI components. The backend asserts the district from the JWT; the frontend just makes calls.

District switchers (for platform-admin users with multi-district access) are a separate flow that issues a new JWT with the chosen district claim, not a parameter passed in regular API calls.

## When you forget

The failure mode of forgetting `lea_id` is invisible until a customer notices: LEA A sees LEA B's data. The customer notification is the only signal. **There is no fallback safety net** — application code is the enforcement layer. Hence the discipline.

Code review for `lea_id` is a P0 review item, not a nice-to-have.
