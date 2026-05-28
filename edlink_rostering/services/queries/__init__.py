"""Per-aggregate query modules.

Each module exposes typed read functions for one aggregate (syncs,
quarantine, audit, timeline, reconciliation, leas). Routers call into
these instead of inlining ``sqlalchemy.text()``; the queries return
dataclasses that the router maps to Pydantic response models.

Rationale: the previous router pattern interleaved HTTP plumbing,
multi-tenant filter logic, and raw SQL. That worked at POC scale but
makes the per-LEA invariant a copy-paste convention and the queries
untestable without spinning up a TestClient. Each module here is
async-session-factory based, returns typed rows, and is unit-testable
against a real Postgres without going through FastAPI.

Migrate one router at a time; the boundary stays clean even while the
migration is in flight.
"""
