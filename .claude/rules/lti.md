---
paths:
  - api/src/edlink_rostering/connectors/lti/**/*.py
  - api/src/edlink_rostering/api/lti/**/*.py
  - api/src/edlink_rostering/core/launch/**/*.py
---

# LTI 1.3 with Advantage discipline

Rules when implementing the Tool side of LTI 1.3 with Advantage. Reference: the LTI 1.3 spec at https://www.imsglobal.org/spec/lti/v1p3 and LTI Advantage at https://www.1edtech.org/standards/lti.

## Use a library; don't roll your own

LTI 1.3 has subtle cryptography and validation requirements. **Use `pylti1.3` or equivalent.** Wrap it in the application's `Connector` abstraction so the rest of the codebase doesn't need to know LTI internals.

Recommended library:

- `pylti1.3` — https://github.com/dmitry-viskov/pylti1.3 — most mature Python implementation, supports FastAPI integration with adapters

If a feature isn't supported by the library, prefer extending the library to rolling your own.

## OIDC launch flow integrity

The launch flow has three phases and **every phase must validate**:

1. **OIDC login initiation** — POST from LMS to Tool. Capture issuer, login_hint, target_link_uri. Generate state and nonce. Return 302 redirect to LMS authorize endpoint.

2. **LMS authorize → Tool launch URL** — POST from LMS to Tool. Contains `id_token` JWT.

3. **JWT validation:**
   - Signature against LMS JWKS endpoint (cached, refreshed on signature failure)
   - `iss`, `aud`, `exp`, `iat` claims
   - `nonce` matches what was issued in step 1
   - `state` is your CSRF token (you generated it, you verify it)

```python
async def launch_handler(request: Request) -> Response:
    id_token = await request.form().get("id_token")
    state = await request.form().get("state")

    # Validate state against what we issued
    if not state_valid(state, request.cookies.get("lti_state")):
        raise LTILaunchError("state mismatch")

    # Validate JWT signature and claims
    message_launch = await lti_message_launch.validate(id_token, state)

    # Use the validated launch context
    return await render_for_launch_context(message_launch)
```

**Never skip JWT validation.** Never trust id_token claims unless the signature has been verified.

## JWKS caching

The LMS's JWKS endpoint provides public keys for JWT verification. Cache the JWKS response:

- TTL: 24 hours
- On signature failure, re-fetch JWKS and retry once (handles rotation)
- Per-issuer cache key

```python
class JWKSCache:
    async def get_jwks(self, issuer: str) -> dict:
        cached = await self.store.get(f"jwks:{issuer}")
        if cached and not cached.is_stale():
            return cached.keys
        fresh = await self._fetch_from_issuer(issuer)
        await self.store.set(f"jwks:{issuer}", fresh, ttl=86400)
        return fresh.keys
```

## Deployment key is (iss, deployment_id)

One LMS instance can host multiple deployments (e.g., per sub-account). The deployment is keyed by `(iss, deployment_id)`, not just `iss`.

```python
class LTIDeployment(Base):
    __tablename__ = "lti_deployment"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    lea_id: Mapped[LeaId] = mapped_column(index=True)
    issuer: Mapped[str] = mapped_column(index=True)  # e.g., https://canvas.instructure.com
    deployment_id: Mapped[str]
    client_id: Mapped[str]
    auth_login_url: Mapped[str]
    auth_token_url: Mapped[str]
    key_set_url: Mapped[str]
    private_key_id: Mapped[str]  # reference to key in secret manager

    __table_args__ = (
        UniqueConstraint("issuer", "deployment_id"),
    )
```

The composite key `(issuer, deployment_id)` is the deployment identifier. **Don't shortcut to just `issuer`.**

## Service calls: client_assertion JWT

Outbound service calls (AGS, NRPS) use JWT client_assertion grant. **Sign with the Tool's private key.** Public key is exposed at the Tool's JWKS endpoint for the LMS to verify.

```python
async def get_service_token(
    self,
    deployment: LTIDeployment,
    scopes: list[str],
) -> str:
    private_key = await self.secret_manager.get(deployment.private_key_id)

    assertion = jwt.encode(
        payload={
            "iss": deployment.client_id,
            "sub": deployment.client_id,
            "aud": deployment.auth_token_url,
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
            "jti": str(uuid4()),
        },
        key=private_key,
        algorithm="RS256",
    )

    response = await self.http_client.post(
        deployment.auth_token_url,
        data={
            "grant_type": "client_credentials",
            "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
            "client_assertion": assertion,
            "scope": " ".join(scopes),
        },
    )
    response.raise_for_status()
    return response.json()["access_token"]
```

Cache service tokens by `(deployment_id, scopes_hash)` until near expiry.

## AGS line item discipline

When pushing grades via Assignment and Grade Services:

1. **Use `resourceLinkId` from the launch as the line item's resource link.** This ties scores to the correct LMS resource.
2. **Don't create new line items per score push.** Get or create the line item once per `(deployment, resource_link_id)`.
3. **`activityProgress` and `gradingProgress`** are the fields that determine score visibility. `gradingProgress: FullyGraded` marks the score as final.

```python
async def push_grade(
    self,
    deployment: LTIDeployment,
    resource_link_id: str,
    user_sub: str,
    score: float,
    max_score: float,
) -> None:
    line_item = await self.get_or_create_line_item(deployment, resource_link_id)
    await self.http_client.post(
        f"{line_item.lineitems_endpoint}/{line_item.id}/scores",
        headers={"Authorization": f"Bearer {await self.get_service_token(...)}"},
        json={
            "userId": user_sub,
            "scoreGiven": score,
            "scoreMaximum": max_score,
            "activityProgress": "Completed",
            "gradingProgress": "FullyGraded",
            "timestamp": datetime.now(UTC).isoformat(),
        },
    )
```

## NRPS — fetch class roster

When the application needs the full class roster (not just the launching user), use Names and Roles Provisioning Services.

```python
async def fetch_class_roster(
    self,
    deployment: LTIDeployment,
    context_memberships_url: str,
) -> list[NRPSMember]:
    members = []
    next_url = context_memberships_url
    while next_url:
        response = await self.http_client.get(
            next_url,
            headers={"Authorization": f"Bearer {await self.get_service_token(...)}"},
        )
        body = response.json()
        members.extend(body["members"])

        # NRPS uses Link header for pagination
        next_url = self._extract_next_link(response.headers.get("Link"))

    return members
```

NRPS members have `user_id`, role list, and (where allowed by LMS) name and email. Privacy settings on the LMS side may redact name/email; handle missing fields gracefully.

## Custom claims for deployment config

Per-district configuration travels through custom claims in the id_token:

```json
"https://purl.imsglobal.org/spec/lti/claim/custom": {
  "lea_id": "d-123",
  "product_variant": "iep_progress"
}
```

LMS admins configure these when setting up the LTI deployment. The application reads them at launch:

```python
custom_claims = id_token.get("https://purl.imsglobal.org/spec/lti/claim/custom", {})
lea_id = custom_claims.get("lea_id")
```

Validate custom claims against the deployment's allowed config. **Don't blindly trust custom claims** — they come from the LMS but were set by an admin, who could set them wrong.

## Cookies and SameSite

LTI launches happen inside an LMS iframe. Cross-origin cookies require `SameSite=None; Secure`.

```python
response.set_cookie(
    key="lti_session",
    value=session_token,
    httponly=True,
    secure=True,
    samesite="none",  # required for iframe cross-origin
    max_age=3600,
)
```

Safari historically has issues with third-party iframe cookies. Test in Safari before declaring a launch flow working.

## Deep linking 2.0

If the application supports Deep Linking (teacher picks specific content from inside the LMS authoring UI), this is a separate launch type with its own message_type:

```
LtiDeepLinkingRequest → Tool shows picker UI → Tool POSTs LtiDeepLinkingResponse back to LMS
```

The response is itself a signed JWT containing the selected resource(s). Use the library's deep-linking helpers; don't construct the response JWT manually.

## Dynamic Registration

Dynamic Registration is the OpenID Connect-based protocol for automatic LTI setup. Supported by Moodle 4+, Canvas (partial), Brightspace, Blackboard.

If implementing, follow the 1EdTech Dynamic Registration spec. The flow:

1. LMS provides registration URL to admin
2. Admin enters URL in Tool
3. Tool fetches LMS's OpenID config
4. Tool POSTs its registration to LMS
5. LMS returns client_id
6. Tool stores deployment record

Manual registration is the fallback when Dynamic isn't supported.

## Multiple LMS instance variants

Canvas has different endpoint URLs for production vs beta vs test:

- Production: `https://sso.canvaslms.com/api/lti/security/jwks`
- Beta: `https://sso.beta.canvaslms.com/api/lti/security/jwks`
- Test: `https://sso.test.canvaslms.com/api/lti/security/jwks`

The deployment record stores the specific endpoints. Don't hardcode.

## Public/private keys

Each application-LMS deployment has its own RSA key pair:

- **Private key** stored in secret manager, never logged
- **Public key** exposed at the Tool's JWKS endpoint

Rotation is per-deployment. Library helpers typically handle the JWKS exposure.

## Testing LTI

Three layers:

1. **Unit tests** with stubbed JWTs (the library has helpers)
2. **Component tests** with a mock LMS that issues real JWTs against a known key pair
3. **Integration tests** against actual LMS instances (Canvas Free for Teachers, Moodle local install, etc.)

Test fixtures should include:

- Successful launch with student role
- Successful launch with teacher role
- Failed launch with invalid signature
- Failed launch with expired token
- Failed launch with mismatched nonce
- AGS score push round-trip
- NRPS class roster fetch with pagination

## Common gotchas

| Gotcha | Fix |
|---|---|
| `SameSite=Lax` cookies break iframe launches | Use `SameSite=None; Secure` |
| Caching JWKS too aggressively | TTL 24h, refresh on signature failure |
| Treating `iss` as deployment key | Use `(iss, deployment_id)` |
| Hard-coding Canvas's `https://sso.canvaslms.com` | Store per-deployment |
| Creating new line items per score | Reuse existing line item by `resourceLinkId` |
| Forgetting `gradingProgress: FullyGraded` | Scores invisible to teacher until set |
| Trusting claims without JWT validation | Always validate first |
| Hard-coding scope strings | Use library constants |

## Cross-references

- LTI 1.3 spec — https://www.imsglobal.org/spec/lti/v1p3
- LTI Advantage — https://www.1edtech.org/standards/lti
- `docs/concepts/auth-patterns.md` — auth patterns catalog including LTI
- `.claude/rules/integration-protocol.md` — broader connector contract
- `.claude/rules/security.md` — JWT validation, cookies, CORS
