---
paths:
  - edlink_rostering/**/*.py
  - api/tests/**/*.py
  - alembic/versions/**/*.py
---

# Python code quality

Conventions for Python code in this project. Backend stack: FastAPI + Pydantic v2 + SQLAlchemy 2.x + Alembic.

## Type hints

**Every public function and method has full type hints.** This is non-negotiable.

```python
# Good
async def get_student(
    student_id: StudentId,
    lea_id: LeaId,
    session: AsyncSession,
) -> Student | None:
    ...

# Bad — missing types
async def get_student(student_id, lea_id, session):
    ...
```

Use modern union syntax (`Student | None` not `Optional[Student]`) and `list[X]`, `dict[K, V]` (not `List`, `Dict` from `typing`). Python 3.13 supports these natively.

For domain types, prefer `NewType` or branded types over raw `str`/`int`:

```python
from typing import NewType
StudentId = NewType("StudentId", str)
LeaId = NewType("LeaId", str)
```

This catches "passed a lea_id where student_id was expected" at type-check time.

## Async by default in I/O paths

Any function that performs I/O (database query, HTTP call, file read) is `async def`. Sync code is reserved for pure computation.

```python
# Good
async def fetch_roster(connector: Connector, lea_id: LeaId) -> Roster:
    response = await connector.get_users(lea_id)
    return parse_roster(response)

# Bad — blocks the event loop
def fetch_roster(connector: Connector, lea_id: LeaId) -> Roster:
    response = connector.get_users(lea_id)  # this would be sync I/O
    return parse_roster(response)
```

Use `httpx.AsyncClient` for outbound HTTP, `asyncpg`-backed SQLAlchemy async sessions for DB, `aiofiles` for file I/O.

Never call sync I/O from an async function without wrapping it in `asyncio.to_thread` or `run_in_executor`. Bare sync I/O blocks the event loop and stalls every other request.

## Pydantic v2 at the boundary

Pydantic models validate every request body, response body, and connector input/output. **Validation is at the boundary, not scattered throughout business logic.**

```python
# Good — boundary validation
class RosterSyncRequest(BaseModel):
    lea_id: LeaId
    full_refresh: bool = False
    since: datetime | None = None

    @field_validator("since")
    @classmethod
    def validate_since_not_future(cls, v: datetime | None) -> datetime | None:
        if v and v > datetime.now(UTC):
            raise ValueError("since cannot be in the future")
        return v

@router.post("/roster/sync")
async def sync_roster(request: RosterSyncRequest) -> SyncResponse:
    # request is already validated; business logic doesn't re-check
    return await roster_service.sync(request)
```

Use `model_config = ConfigDict(strict=True)` for strict type coercion on canonical model classes. Don't use it on FastAPI request models (FastAPI's default Pydantic integration handles request parsing well).

## Avoid `Any`

Treat `Any` like `unsafe` in Rust — it's an escape hatch with a cost. If you need it, comment why:

```python
# Justified Any: third-party callback signature requires Any
def webhook_handler(payload: dict[str, Any]) -> Response:
    ...
```

If you find yourself reaching for `Any` because a type is hard to express, the type is often telling you something — usually that the domain isn't modeled well.

## Errors are explicit

Don't bury errors in generic exceptions. Define domain-specific exceptions:

```python
# Good
class IEPNotFoundError(EdlinkError):
    """Raised when an IEP lookup returns no result."""

class IEPTimelineViolationError(EdlinkError):
    """Raised when an IDEA timeline deadline is exceeded."""

class ConnectorAuthError(EdlinkError):
    """Raised when a connector cannot authenticate to its upstream."""

# Bad
raise Exception("not found")
raise ValueError("oops")
```

Inherit from a common `EdlinkError` base so middleware can handle them uniformly.

Don't `except Exception:` without re-raising or logging. Catching everything is how silent failures happen.

## Dependency injection via FastAPI Depends

Use FastAPI's `Depends` for wiring repositories, services, and external clients. **Don't import singletons; inject dependencies.**

```python
# Good
async def get_iep_service(
    session: AsyncSession = Depends(get_db_session),
    audit_log: AuditLog = Depends(get_audit_log),
) -> IEPService:
    return IEPService(session=session, audit_log=audit_log)

@router.get("/ieps/{iep_id}")
async def read_iep(
    iep_id: IEPId,
    service: IEPService = Depends(get_iep_service),
) -> IEPResponse:
    return await service.get(iep_id)
```

Injection makes testing straightforward (override deps in test fixtures) and keeps dependencies visible at the call site.

## No business logic in routes

Routes are translation layers (HTTP → domain → HTTP). Business logic lives in service classes.

```python
# Good
@router.post("/ieps/{iep_id}/goals")
async def create_goal(
    iep_id: IEPId,
    goal_data: GoalCreateRequest,
    service: IEPService = Depends(get_iep_service),
) -> GoalResponse:
    goal = await service.add_goal(iep_id, goal_data)
    return GoalResponse.from_domain(goal)

# Bad — logic in route
@router.post("/ieps/{iep_id}/goals")
async def create_goal(iep_id: IEPId, goal_data: GoalCreateRequest, db: AsyncSession):
    iep = await db.get(IEP, iep_id)
    if not iep:
        raise HTTPException(404)
    if iep.status == "closed":
        raise HTTPException(400, "Cannot add goal to closed IEP")
    goal = Goal(...)
    db.add(goal)
    await db.commit()
    return goal
```

Routes don't import `AsyncSession` directly. Routes don't enforce invariants. Routes are dumb plumbing.

## Repository pattern for persistence

Don't sprinkle ORM queries through service code. Define a repository per aggregate root:

```python
class IEPRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, iep_id: IEPId, lea_id: LeaId) -> IEP | None:
        result = await self.session.execute(
            select(IEP).where(
                IEP.id == iep_id,
                IEP.lea_id == lea_id,  # multi-tenancy enforced here
            )
        )
        return result.scalar_one_or_none()
```

LEA ID is enforced at the repository layer per the `multi-tenancy` rule. Services don't bypass repositories to hit the session directly.

## Logging is structured, never with PII

```python
import structlog
log = structlog.get_logger()

# Good
log.info("iep_created", iep_id=iep.id, lea_id=iep.lea_id)

# Bad — student PII in logs
log.info(f"Created IEP for {student.first_name} {student.last_name}")
```

See `.claude/rules/security.md` for the full no-PII-in-logs discipline.

## Test discipline

- Every public function has at least one unit test
- Test names describe behavior: `test_iep_timeline_alerts_fire_when_deadline_within_7_days`
- Fixtures live in `conftest.py`; deterministic UUIDs per `.claude/rules/seed-data.md`
- Async tests use `pytest-asyncio` and `@pytest.mark.asyncio`
- Mock external HTTP with `respx` or `httpx_mock`, not generic patches
- Integration tests use real Postgres (via testcontainers or a local dev DB), not mocked sessions

## Style and formatting

- `ruff` is the source of truth for formatting and linting (replaces black + isort + flake8)
- `mypy` is the source of truth for type checking (strict mode for `core/`, `canonical/`, `compliance/`; looser for `infrastructure/`)
- 100 character line length
- Trailing commas in multi-line collections (ruff handles)
- Use f-strings, not `.format()` or `%`
- Imports: standard lib → third-party → first-party, separated by blank lines

## Decision capture

When you choose a non-obvious pattern (e.g., why this is a class instead of a function, why this dependency boundary), capture it in a code comment with `[Rationale]:` prefix or open an ADR if it's structural.

```python
# [Rationale]: We deliberately chose to make IEPService stateful (holds
# a session and audit_log) rather than passing them per-call. This matches
# the unit-of-work pattern and keeps service methods reading naturally.
class IEPService:
    ...
```

## Things to avoid

- Mutable default arguments (`def f(x=[]):`) — classic Python footgun
- String concatenation in queries (use SQLAlchemy parameter binding always)
- `eval` and `exec` — never, no exception
- Module-level state for anything that should be per-request
- Bare `except:` clauses
- `print()` statements outside of one-off scripts
- Re-implementing standard library functionality (look in `itertools`, `functools`, `pathlib` first)

