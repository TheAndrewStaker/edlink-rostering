---
paths:
  - edlink_rostering/**/*.py
  - alembic/versions/**/*.py
  - infrastructure/**/*.py
---

# Security

OWASP-shape security rules for the Python/FastAPI backend, plus the student-data-specific protections required by FERPA, IDEA, and COPPA. Reference: `docs/security/SECURITY_ARCHITECTURE.md`.

## No PII in logs

**Student PII never appears in log lines.** This includes:

- First name, last name, preferred name
- Date of birth
- Social Security Number, state IDs (sometimes), district IDs that are SSN-derived
- Home address, phone, email (parent and student)
- Race, ethnicity, language
- IEP content (goals, services, evaluation reports — the body of the IEP)
- Disability category or status
- Disciplinary records
- Health records
- Free/reduced lunch status

**Allowed:** opaque IDs (lea_id, student_id, iep_id), aggregate counts, status enums.

```python
# Good
log.info("iep_amended", iep_id=iep.id, lea_id=auth.lea_id, fields_changed=["services", "goals"])

# Bad — PII in log
log.info(f"Updated IEP for {student.first_name} {student.last_name}")
log.info("iep_amended", student_name=student.full_name)

# Bad — health/educational status
log.info("alert_fired", reason=f"Student {student.id} has emotional disturbance designation")
```

When logging errors with payloads, **redact before logging**:

```python
def redact_pii(payload: dict) -> dict:
    """Return a copy of payload with known PII keys redacted."""
    sensitive_keys = {"firstName", "lastName", "givenName", "familyName",
                      "dateOfBirth", "ssn", "homeAddress", "email", "phone",
                      "preferredFirstName", "race", "ethnicity"}
    return {k: ("[REDACTED]" if k in sensitive_keys else v) for k, v in payload.items()}

log.error("connector_payload_invalid", payload=redact_pii(payload), error=str(e))
```

The audit log is different — see "Audit logging" below. Audit logs DO capture identifiers but never the full content body.

## Validation at the boundary with @Valid-equivalent

Every request body is validated by Pydantic at the FastAPI boundary. **Never trust unvalidated input downstream.**

```python
# Good
@router.post("/ieps")
async def create_iep(
    request: IEPCreateRequest,  # Pydantic validates at boundary
    service: IEPService = Depends(get_iep_service),
) -> IEPResponse:
    return await service.create(request)

# Bad — accepting raw dict skips validation
@router.post("/ieps")
async def create_iep(payload: dict, service: IEPService = Depends(get_iep_service)):
    return await service.create(payload)
```

Use strict Pydantic models. Provide `field_validator` for domain-specific checks. Don't validate at the service layer — validation has already happened at the boundary.

## JWT authentication for all user routes

Every user-facing route requires authentication. Anonymous routes are explicitly listed and minimal (login, health check, OIDC callback).

```python
@router.get("/students/{student_id}")
async def get_student(
    student_id: StudentId,
    auth: AuthContext = Depends(get_auth_context),  # mandatory
    service: StudentService = Depends(get_student_service),
):
    ...
```

`get_auth_context` validates the JWT, extracts claims, and constructs the auth context. **If JWT validation fails, the request is rejected before reaching the route.**

JWTs are signed with platform-controlled keys (RS256 for asymmetric, HS256 for internal use only). Token TTLs are short (30 minutes for access tokens, 7 days for refresh tokens, configurable).

## OWASP API top 10 checklist

For every new endpoint, check against the OWASP API top 10:

| OWASP | How we mitigate |
|---|---|
| API1: Broken Object Level Authz | LEA scoping per `.claude/rules/multi-tenancy.md`; RBAC checks |
| API2: Broken Authentication | JWT validation in `get_auth_context`; short TTLs |
| API3: Broken Property Level Authz | Pydantic response models; never return full DB objects |
| API4: Unrestricted Resource Consumption | Rate limiting via middleware; query pagination limits |
| API5: Broken Function Level Authz | Role checks at route entry; platform-admin paths separated |
| API6: Unrestricted Access to Sensitive Business Flows | Mutation endpoints require explicit permission |
| API7: SSRF | Outbound HTTP allow-list; partner URLs validated |
| API8: Security Misconfiguration | Static config; secrets via env or secret manager only |
| API9: Improper Inventory Management | API versioned; deprecated endpoints flagged in docs |
| API10: Unsafe Consumption of APIs | Validate connector responses with Pydantic; reject malformed |

## No PII in URLs

URLs are logged by every web server, proxy, and CDN in the request path. Don't put PII in them.

```python
# Good — IDs only
GET /students/{student_id}
GET /ieps/{iep_id}/goals

# Bad — PII in path
GET /students/alice-smith
GET /ieps/2026-01-15-alice-smith
```

Same for query strings. If you need to filter by name (debug, admin tool), use POST with body.

## No PII in error messages

Error messages return generic descriptions. PII goes in the audit log, not the API response.

```python
# Good
raise StudentNotFoundError("student not found in district")
# API returns: {"error": "student_not_found", "message": "Student not found in district"}

# Bad
raise StudentNotFoundError(f"student {student.first_name} {student.last_name} not in district")
```

Stack traces are never exposed in production responses. Server logs (with redacted PII per above) keep full detail.

## Secrets handling

- **Never in code.** Not in source, not in committed config, not in docstrings.
- **Environment variables for local dev** — `.env` files in gitignore, sample `.env.example` committed.
- **Secret manager in production** — AWS Secrets Manager, Google Secret Manager, etc.
- **Rotation discipline** — webhook secrets, OAuth credentials, JWT signing keys rotate on a schedule. Document rotation procedures in runbooks.

## SQL injection: use parameter binding always

```python
# Good — SQLAlchemy parameter binding
result = await session.execute(
    select(Student).where(Student.id == student_id, Student.lea_id == lea_id)
)

# Bad — string concatenation
query = f"SELECT * FROM student WHERE id = '{student_id}'"
result = await session.execute(text(query))
```

Even when the input looks safe, use parameter binding. The discipline is the point.

If you absolutely need dynamic SQL (rare), use SQLAlchemy's `bindparam` or `text(...).bindparams(...)`.

## Cross-Site Scripting (XSS)

React's default escaping handles most XSS, but watch for:

- `dangerouslySetInnerHTML` — never with untrusted input
- `href={user_provided_url}` — validate URL scheme (no `javascript:`)
- iframe `src` from untrusted sources

The frontend rules in `.claude/rules/frontend-components.md` cover this in more depth.

## CORS configuration

Production CORS is restrictive. Allowed origins are explicit; no `*` in production. Credentials only enabled for known origins.

```python
# Good
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://app.edlink.example", "https://district-portal.edlink.example"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    allow_headers=["Authorization", "Content-Type"],
)
```

LTI launch endpoints have separate CORS rules because they receive cross-origin POST from LMSes.

## CSP and security headers

Standard security headers on all responses:

- `Strict-Transport-Security: max-age=31536000; includeSubDomains`
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: SAMEORIGIN` (but LTI launch routes get `ALLOWALL` because they're iframed)
- `Content-Security-Policy: default-src 'self'; ...` (tighten per route requirements)

Use a middleware to apply uniformly. Override per-route only with justification.

## Audit logging

Every operation on student data goes through audit logging. **Required, not optional.**

The audit record includes:

- `timestamp` — UTC ISO 8601
- `actor_id` — user or system actor identifier
- `actor_role` — role at time of access
- `lea_id` — multi-tenancy scope
- `operation` — verb (e.g., `iep_read`, `iep_create`, `goal_update`, `roster_sync`)
- `resource_type` — entity type (e.g., `iep`, `student`, `goal`)
- `resource_id` — entity identifier
- `outcome` — `success`, `permission_denied`, `not_found`, `error`
- `source_ip` — for human-actor requests
- `request_id` — correlation with application logs

Implementation pattern via decorator or middleware:

```python
@audit_log(operation="iep_read", resource_type="iep")
async def get_iep(self, iep_id: IEPId, lea_id: LeaId) -> IEP:
    ...
```

The decorator captures actor context, populates the audit record, and writes it asynchronously (don't block the operation on the audit write — write to a queue, persist to dedicated audit storage).

Audit logs are retained separately from application logs and for a longer period (typically 7 years for FERPA evidentiary basis).

## Webhook signature verification

Every inbound webhook is signed; signatures are verified before any payload is parsed.

```python
async def verify_webhook(
    body: bytes,
    signature_header: str,
    timestamp_header: str,
    secret: bytes,
) -> None:
    # 1. Reject if timestamp is outside replay window
    timestamp = int(timestamp_header)
    if abs(time.time() - timestamp) > 300:  # 5 minutes
        raise WebhookReplayError()

    # 2. Compute expected signature
    payload = f"{timestamp}.".encode() + body
    expected = hmac.new(secret, payload, hashlib.sha256).hexdigest()

    # 3. Constant-time compare
    if not hmac.compare_digest(expected, signature_header):
        raise WebhookSignatureInvalid()

    # 4. Reject duplicate event_id (replay protection layer 2)
    event_id = json.loads(body).get("event_id")
    if await event_dedup_store.has_seen(event_id):
        raise WebhookReplayError()
    await event_dedup_store.mark_seen(event_id, ttl=86400)
```

This pattern is in `architecture/connector-protocol.md` in more detail.

## Encryption

- **In transit:** TLS 1.3, modern cipher suites only. No TLS 1.0/1.1 anywhere.
- **At rest:** Database-level encryption (managed Postgres encrypted-at-rest by cloud provider). Per-field encryption for elevated-sensitivity fields (e.g., SSN if stored) using a key from the secret manager.

Don't roll your own crypto. Use vetted libraries (`cryptography` package).

## Rate limiting

Every public endpoint is rate-limited. Limits are:

- Per-IP for unauthenticated endpoints
- Per-user for authenticated endpoints
- Per-district for ingest endpoints (avoid one district saturating the system)

Use a Redis-backed rate limiter or cloud provider rate-limit features.

## Things that are violations

Single-line violations that should block PRs:

- `eval(`, `exec(` on any code path
- `subprocess` with `shell=True` and dynamic input
- `pickle` deserialization of untrusted data
- `yaml.load` (use `yaml.safe_load`)
- Logging at INFO or higher with anything containing student PII
- `except Exception: pass`
- Hardcoded secrets, hardcoded credentials, hardcoded API keys

## Static analysis

- `bandit` runs in CI for Python security linting
- `ruff` rules for security (`S` category) enabled
- Dependency scanning via `pip-audit` or equivalent
- Frontend: `npm audit` in CI

Failing security checks block merge.

## Penetration testing

Annual third-party pen test. Findings fed into the security backlog. Critical findings have SLA-tracked remediation. Reference report path in `docs/security/SECURITY_ARCHITECTURE.md`.
