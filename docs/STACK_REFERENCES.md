# Stack references

The anti-drift anchor for stack patterns. AI training data drifts; this document captures the current stable versions the framework uses or targets, with vendor doc URLs.

**Read before making stack-related decisions or writing infrastructure code.**

Last reviewed: May 2026. Re-verify quarterly.

## Backend stack

| Component | In project | Current stable | Status | Vendor docs |
|---|---|---|---|---|
| Python | `[VERIFY: 3.13 or 3.14]` | 3.14 (GA Oct 2025; 3.14.5 May 2026) | Active. 3.13 is the safe pick today; 3.14 is GA and production-eligible | https://docs.python.org/3/ |
| FastAPI | `[VERIFY]` | 0.115+ | Active | https://fastapi.tiangolo.com/ |
| Pydantic | `[VERIFY]` | 2.x | Active | https://docs.pydantic.dev/latest/ |
| SQLAlchemy | `[VERIFY]` | 2.x | Active | https://docs.sqlalchemy.org/en/20/ |
| Alembic | `[VERIFY]` | 1.13+ | Active | https://alembic.sqlalchemy.org/ |
| PostgreSQL | `[VERIFY]` | 16+ | Active | https://www.postgresql.org/docs/16/ |
| Redis | `[VERIFY]` | 7.4+ | Active | https://redis.io/docs/latest/ |
| Celery / Dramatiq / RQ | `[VERIFY which]` | Various | Active | per-vendor |
| pytest | `[VERIFY]` | 8.x | Active | https://docs.pytest.org/ |
| pytest-asyncio | `[VERIFY]` | 0.23+ | Active | https://pytest-asyncio.readthedocs.io/ |
| httpx | `[VERIFY]` | 0.27+ | Active | https://www.python-httpx.org/ |
| tenacity (retry) | `[VERIFY]` | 9.x | Active | https://tenacity.readthedocs.io/ |
| structlog (logging) | `[VERIFY]` | 24.x | Active | https://www.structlog.org/ |
| uv or poetry | `[VERIFY which]` | uv 0.4+ / poetry 1.8+ | Active | https://docs.astral.sh/uv/ / https://python-poetry.org/ |
| ruff | `[VERIFY]` | 0.5+ | Active | https://docs.astral.sh/ruff/ |
| mypy | `[VERIFY]` | 1.11+ | Active | https://mypy.readthedocs.io/ |

## Frontend stack

| Component | In project | Current stable | Status | Vendor docs |
|---|---|---|---|---|
| React | `[VERIFY]` | 18.3 (19 in beta) | Active | https://react.dev/ |
| TypeScript | `[VERIFY]` | 5.5+ | Active | https://www.typescriptlang.org/docs/ |
| Vite or Next.js | `[VERIFY which]` | Vite 5.4 / Next 15 | Active | https://vite.dev/ / https://nextjs.org/ |
| React Router / TanStack Router | `[VERIFY]` | various | Active | per-vendor |
| TanStack Query | `[VERIFY]` | 5.x | Active | https://tanstack.com/query/latest |
| MUI / Mantine / Tailwind+shadcn | `[VERIFY which]` | various | Active | per-vendor |
| Vitest | `[VERIFY]` | 2.x | Active | https://vitest.dev/ |
| Playwright | `[VERIFY]` | 1.48+ | Active | https://playwright.dev/ |

## Integration ecosystem

| Standard / Service | Current version | Last verified | Source |
|---|---|---|---|
| OneRoster | 1.2 | 2026-05-11 | https://www.imsglobal.org/spec/oneroster/v1p2 |
| Ed-Fi Data Standard | 6.1 | 2026-05-11 | https://docs.ed-fi.org/reference/data-exchange/data-standard/whats-new/whats-new-v61/ |
| Ed-Fi SEDM | Early access in 6.1 | 2026-05-11 | https://docs.ed-fi.org/reference/data-exchange/data-standard/whats-new/whats-new-v61/ |
| Ed-Fi ODS/API | 7.3.2 | 2026-05-11 | https://docs.ed-fi.org/ |
| Ed-Fi OneRoster Service | Production, 1EdTech-certified for OneRoster v1.2 Rostering Core | 2026-05-12 | https://docs.ed-fi.org/reference/oneroster/ |
| LTI | 1.3 with Advantage | 2026-05-11 | https://www.imsglobal.org/spec/lti/v1p3 |
| Clever API | v3.1 (LMS Connect) | 2026-05-11 | https://dev.clever.com/docs/api-v31 |
| Ednition RosterStream | Current | 2026-05-11 | https://ednition.com/ |
| EdLink | API v2 | 2026-05-11 | https://ed.link/docs/guides/v2.0/overview |

## Library selection rationale

### Why FastAPI over Django or Flask

FastAPI's strengths align with integration framework work:
- Native async support (critical for partner API I/O)
- Pydantic-based validation (type-checked request/response models)
- OpenAPI generation (automatic API docs)
- Mature dependency injection (clean wiring for connectors, repositories)

Django is heavier and synchronous-first; Flask requires more wiring for the same baseline. FastAPI is the right shape.

### Why Pydantic v2 over dataclasses or attrs

Pydantic v2 gives:
- Validation at the system boundary (FastAPI integration)
- JSON schema generation (useful for partner contracts)
- Runtime type-checking (catches schema drift earlier)
- Performance comparable to manually-tuned code

Dataclasses are simpler but lack validation. attrs is excellent but FastAPI/Pydantic integration is tighter.

### Why SQLAlchemy 2.x over an async ORM like Tortoise or Beanie

SQLAlchemy 2.x has native async support, the largest ecosystem, the cleanest migration story via Alembic, and broad community familiarity. Tortoise and Beanie are good but have smaller ecosystems and shorter track records for production-critical data.

### Why TanStack Query for server state

TanStack Query (formerly React Query) is the established choice for server state in React. Handles caching, invalidation, retry, refetch-on-mount, and reconciliation patterns out of the box. No reason to roll your own.

### Why Playwright for E2E

Playwright over Cypress for: better multi-browser support, better trace viewer, simpler CI integration, better evolution velocity. Either works.

## Anti-drift rules for AI agents

When agents are reasoning about library APIs:

1. **Don't trust training data on minor version specifics.** Check the version in `pyproject.toml` or `package.json` and read the vendor docs at that version.

2. **Don't invent function signatures.** If you're not sure how to call something, look it up. The actual signature is in the package source, not in your priors.

3. **Don't assume defaults haven't changed.** Pydantic v1 → v2 had significant default changes; SQLAlchemy 1.x → 2.x changed query API. Verify defaults at the version in use.

4. **Don't conflate similar libraries.** FastAPI is not Flask. SQLAlchemy is not Django ORM. The patterns differ even when they look similar.

5. **When in doubt, search.** A 30-second search of vendor docs beats five minutes of code that doesn't compile.

## Cloud and infrastructure

| Component | Current | Notes |
|---|---|---|
| Cloud provider | Microsoft Azure | Confirmed (Q-009 / Q-011). |
| Compute | Azure Functions (Flex Consumption) for workers; Azure App Service for the FastAPI admin API | Flex Consumption is scale-to-zero, VNet-integrated, with no enforced execution timeout. One plan hosts the poll timer, reconciliation timer, sync session-trigger, and the Data Feeds webhook HTTP-trigger. |
| Messaging | Azure Service Bus Standard, sessions on (`session_id=lea_id`) | Sessions and duplicate detection ship on Standard; Premium only for private endpoints, geo-replication, or messages over 256 KB. Resolves Q-019. |
| Database | Azure Database for PostgreSQL Flexible Server, zone-redundant HA from day one (General Purpose tier floor) | Burstable cannot do HA. |
| Secrets | Azure Key Vault (one EdLink application secret plus the JWT signing key), reached by managed identity | No per-LEA token in Key Vault; EdLink owns per-LEA tokens, re-fetched by `integration_id`. |
| Scheduler | Azure Functions timer triggers | Poll cadence and daily reconciliation. Resolves Q-020. |
| Container runtime | Docker / containerd (local dev) | |
| Observability | Azure Monitor OpenTelemetry distro to workspace-based App Insights + Log Analytics | Sampling off by default; alert on custom metrics, which are never sampled. |
| Logging | Structured JSON via structlog | Per `.claude/rules/security.md` no-PII rule |
| Static frontend | Azure Static Web Apps, linked to the App Service backend | |

## Migration considerations on the horizon

- **Python 3.14** has been GA since October 2025 (3.14.5 as of May 2026), but the deploy target sets the ceiling: Azure Functions lists Python **3.13 as its highest GA runtime**, with 3.14 only in **Preview** on Flex Consumption (remote build does not yet support 3.14). So the worker code pins **3.13 GA** (supported through October 2029) to match the production runtime and avoid local-vs-prod version skew. A locally installed 3.14 SDK is fine; develop against 3.13 to mirror prod. Revisit when 3.14 reaches GA on Functions with remote-build support.
- **Ed-Fi API v8 (Data Management Service)** Release 8.0 ships July 2026 for **pilot / parallel use in school year 2026-2027** with new relational table design. Production milestone is Release 8.1 in Q4 2026 for school year 2027-2028. Plan consumer-side migration when the application adopts it. Source: https://docs.ed-fi.org/reference/roadmap/api-faq/
- **Ed-Fi ODS/API .NET 10 upgrade** in 7.3.2 ahead of .NET 8 EOL November 2026. Not the application's direct concern but affects state Ed-Fi infrastructure.
- **SEDM stabilization** — early access today; track for transition to stable release.
- **2025 COPPA Rule update** — practice guidance still emerging.

## Shelf life

Re-verify the version columns above:
- **Quarterly:** Major dependencies and standards
- **Before any new partner integration:** All entries that touch that partner

This document is also a record. If you update a version, note the date and reason in commit messages.
