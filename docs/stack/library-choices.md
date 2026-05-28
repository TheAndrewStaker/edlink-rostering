# Library choices

Concrete recommendations for the Python backend libraries. Where a recommendation depends on unconfirmed infrastructure state, that is noted explicitly.

This file complements `docs/STACK_REFERENCES.md`. STACK_REFERENCES is the version anchor (which version of Python, FastAPI, SQLAlchemy). This file is the choice anchor (which OAuth library, which scheduler, which Redis client).

The discipline: every implementing agent should be able to scan this table and know what to import without making a decision. When a decision is genuinely deferred, that's named explicitly.

## Backend library table

| Slot | Recommendation | Alternatives | Rationale | Decision status |
|---|---|---|---|---|
| Web framework | **FastAPI 0.115+** | Litestar, Starlette | Native async, Pydantic v2 integration, Depends DI, OpenAPI gen | Accepted (in CLAUDE.md) |
| Validation | **Pydantic v2** | attrs, msgspec | FastAPI native, runtime validation, JSON schema | Accepted |
| ORM | **SQLAlchemy 2.x async** | Tortoise ORM, SQLModel, Piccolo | Largest ecosystem, Alembic, type-checkable 2.x API | Accepted |
| Migrations | **Alembic 1.13+** | yoyo-migrations | SQLAlchemy-native; reviewed-by-hand discipline per `.claude/rules/alembic.md` | Accepted |
| Database | **PostgreSQL 16+ with pgvector 0.8.2+** | none for primary | Multi-tenant SaaS standard; pgvector co-located per `docs/design/data-architecture.md`. **Pin pgvector ≥ 0.8.2** for CVE-2026-3172 (parallel HNSW index build vulnerability) | Accepted |
| Postgres driver | **asyncpg** | psycopg3 (async) | Faster async path; SQLAlchemy 2.x supports both | Recommended |
| Connection pool | **SQLAlchemy built-in** (asyncpg) | pgbouncer (external) | Built-in pool for app tier; pgbouncer optional in front | Recommended; revisit at scale |
| Cache / event-transport | **Redis 7.4+** | Valkey | Standard; Redis Streams is the provisional event bus | Accepted for cache; event bus provisional |
| Redis client | **redis-py 7.x with hiredis** | aioredis (deprecated/merged) | Async-native; hiredis for parsing speed. **redis-py jumped from 5.x to 7.x in 2025-2026**; verify cluster API changes before adopting | Recommended |
| HTTP client | **httpx 0.27+** | aiohttp | Sync/async parity, modern API, FastAPI ecosystem | Accepted |
| OAuth 2.0 client | **Authlib 1.3+** | raw httpx, oauthlib | Handles client_credentials, JWT client assertion (LTI), token refresh, JWKS caching | Recommended |
| OAuth provider (LTI) | **pylti1.3** | iOSS LTI libs, `lti1p3platform`, in-house fork | Mature pattern; **library itself is inactive (last release Nov 2022; ~3 years stale as of May 2026)**. FastAPI integration; used by Canvas docs | **Recommended pending fork or alternative evaluation.** Pin a specific version; evaluate maintenance burden before LTI work starts. Snyk advisor flags inactive/discontinued. |
| Retry | **tenacity 9.x** | stamina, backoff | Decorator and context manager API, exponential backoff | Accepted (in `.claude/rules/integration-protocol.md`) |
| Webhook signature | **stdlib `hmac` + `hashlib`** | external libs | Constant-time compare with `hmac.compare_digest` is stdlib | Accepted |
| State machine | **python-statemachine 3.x** | transitions, hand-rolled enum + dict | Typed, declarative, Python 3.13+ compatible. Use for LEA authorization state machine. **3.0.0 released Feb 2026**; revisit API per `library-choices.md` quarterly-revisit rule | Recommended |
| Background scheduler | **procrastinate 3.x** | apscheduler, arq, dramatiq | Postgres-backed, durable across restarts, no separate infra. **3.x released April 2026.** | Provisional |
| Event bus transport | **Redis Streams** | RabbitMQ, Kafka, AWS EventBridge | Provisional if Redis is in the stack; simpler ops; sufficient for MVP scale | Provisional |
| Outbox pattern | **Hand-rolled with SQLAlchemy** | Debezium, transactional-outbox lib | Pattern is simple; tied to the canonical schema | Recommended |
| Structured logging | **structlog 24.x** | loguru | structlog binds context, integrates with stdlib logging, no-PII discipline per `.claude/rules/security.md` | Accepted |
| Observability / logging sink | **Azure Application Insights** (`opencensus-ext-azure` or OpenTelemetry → App Insights exporter) | Datadog, Honeycomb, self-hosted Loki | Integrate via OpenTelemetry where possible to keep vendor lock-in shallow | **Accepted** |
| Metrics | **prometheus-fastapi-instrumentator 7.x + prometheus-client**, exported to App Insights | starlette-prometheus | FastAPI-native middleware; per-route latency and counters. App Insights ingests via OTel exporter | Recommended |
| Tracing | **opentelemetry-distro + azure-monitor-opentelemetry-exporter** | Jaeger SDK | Vendor-neutral OTel SDK; Azure-native exporter for App Insights | Recommended |
| Error tracking | **Azure Application Insights** (App Insights surfaces exceptions natively) or **Sentry (sentry-sdk)** if richer error grouping is needed | Honeybadger, Rollbar | Default to App Insights to avoid a second vendor; Sentry if App Insights' error UX proves insufficient | Provisional pending day-one walkthrough |
| Secret manager | **Azure Key Vault** (`azure-keyvault-secrets` + `azure-identity` for Managed Identity auth) | HashiCorp Vault | Managed Identity removes the need for credential bootstrapping | **Accepted** |
| Rate limiter | **slowapi** for API endpoints; **redis-py** primitives for outbound | aioslowapi | slowapi is the FastAPI-Flask-Limiter equivalent | Recommended |
| JSON schema | **Pydantic models** primary; **jsonschema** for external schemas (Ed-Fi descriptors) | fastjsonschema | Use Pydantic where possible; jsonschema for vendor specs | Recommended |
| SFTP client | **asyncssh 2.x** | paramiko (sync), aiosftp | Async-native, used for state IEP systems per `.claude/rules/integration-protocol.md` | Recommended |
| Package manager | **uv 0.4+** | poetry, pip-tools, hatch | Fast, modern, PEP 621 native | Recommended |
| Linter / formatter | **ruff 0.5+** | black + isort + flake8 | Per `.claude/rules/code-quality.md`; replaces three tools | Accepted |
| Type checker | **mypy 1.11+** | pyright | Strict mode for `core/`, `canonical/`, `compliance/`; looser for `infrastructure/` per `.claude/rules/code-quality.md` | Accepted |
| Static security | **bandit + ruff S-rules + pip-audit** | semgrep | Per `.claude/rules/security.md` | Accepted |

## Testing

| Slot | Recommendation | Alternatives | Rationale |
|---|---|---|---|
| Test runner | **pytest 8.x** | unittest | Standard | Accepted |
| Async tests | **pytest-asyncio 0.23+** | anyio test runner | Standard for FastAPI/SQLAlchemy async | Accepted |
| HTTP mocking | **respx 0.21+** | httpx_mock, vcr.py | httpx-native, supports both unit and integration tests | Accepted (in `.claude/rules/code-quality.md`) |
| Database fixtures | **testcontainers-python (Postgres)** | pytest-postgresql, SQLite | Real Postgres in CI; testcontainers is the standard | Recommended |
| Test data factories | **polyfactory 3.x** | factory_boy | Pydantic-native; generates from model schemas. **3.x released Feb 2026** (was 2.x in original draft) | Recommended |
| Property-based | **Hypothesis** | none | For canonical translation tests, idempotency proofs | Optional but encouraged |
| Snapshot testing | **syrupy** | none | For complex canonical outputs (Ed-Fi response → canonical mapper) | Optional |
| Coverage | **coverage.py 7.x via pytest-cov** | none | Standard | Recommended |

## Frontend slot table (post-MVP, but worth recommending now)

Frontend is not in scope for the July MVP per `roadmap/before-july.md`, but the existing product has a frontend; integration with it will surface in week 5-6. See `docs/UI_STANDARDS.md` for fuller conventions.

| Slot | Recommendation | Alternatives | Decision status |
|---|---|---|---|
| Framework | **React 18.3** | React 19 (beta) | Accepted; revisit React 19 in Q3 |
| Build tool | **Vite 5.4** | Next.js 15 | Confirmed for integrations-side UI; verify against existing product build setup |
| Server state | **TanStack Query 5.x** | SWR | Accepted |
| UI library | **Chakra UI** | MUI, Mantine, Tailwind+shadcn | **Accepted** |
| Forms | **react-hook-form 7.x + zod 3.x** | Formik | Recommended |
| Routing | **TanStack Router 1.x** | React Router 6.x | Recommended |
| E2E | **Playwright 1.48+** | Cypress | Accepted |

## What's intentionally not chosen here

- **AI / embedding provider.** Pending FERPA-compliant subprocessor confirmation.
- **Vector DB.** pgvector is the recommended default per `docs/design/data-architecture.md`.
- **Cloud-specific managed services** (queue, scheduler, secret manager) depend on cloud provider choice.
- **CI runner.** TBD.
- **APM / log aggregation.** Sentry recommended for error tracking; broader APM (Datadog, New Relic, OpenTelemetry collector) TBD.

## Major version bumps since the original drafting (May 2026 currency check)

These don't change the recommendation but the floors in the table reflect current stable. Worth tracking:

| Library | Was | Now | Notes |
|---|---|---|---|
| pytest | 8.x | **9.0** | Verify pytest-asyncio / pytest-cov compatibility before adopting |
| pytest-asyncio | 0.23+ | **1.3** | Reached 1.0; quarterly-revisit trigger fired |
| mypy | 1.11+ | **2.1** | Verify strict-mode defaults; may require config updates |
| poetry | 1.8+ | **2.4** | Lockfile format changed; not drop-in if the application uses poetry |
| TypeScript | 5.5+ | **6.0** | Verify React, Vite compatibility before upgrading |
| Vite | 5.4 | **8.x** | Three major version jumps; significant |
| Next.js | 15 | **16.2** | Major version bump |
| React | 18.3 | **19.2** | React 19 has been GA since Dec 2024; "19 in beta" framing was stale |

The recommended action: when the actual implementation starts, pin to the current stable and document the floor at that time. The above is informational for the team to know the gap between the bundle's original recommendations and current state.

## When to revisit

Re-verify entries quarterly, or whenever:
- A library hits 1.0 (procrastinate is 3.x, mature; python-statemachine is 3.x, mature)
- A vendor releases a major version (Pydantic v3, SQLAlchemy 3, FastAPI 1.0)
- An existing choice becomes a bottleneck (revisit Redis Streams vs Kafka at >10k events/sec)
- A security advisory affects a recommended library (see pgvector CVE-2026-3172 above)
- A recommended library becomes inactive (see pylti1.3 note above)

## Cross-references

- `docs/STACK_REFERENCES.md` — version anchors and vendor doc URLs
- `.claude/rules/code-quality.md` — patterns for the chosen libraries
- `.claude/rules/integration-protocol.md` — retry, idempotency, token cache patterns
- Decision records for event bus transport, scheduler, and other open questions blocking specific entries above
