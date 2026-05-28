# Auth patterns reference

Authentication patterns used across the rostering framework's integration surface. Every connector uses one or more of these.

Reference for connector framework design in `architecture/connector-framework.md`.

## OAuth 2.0 Client Credentials (with shared secret)

The default B2B auth pattern. Used by the vast majority of EdTech integration partners: Clever, ClassLink, EdLink, Ednition, direct SIS APIs, most IEP vendor APIs, most assessment vendor APIs.

**Onboarding artifacts exchanged:**
- `client_id` (public, identifies your app)
- `client_secret` (shared secret, both sides hold)
- token endpoint URL
- scopes for the access being requested

**Runtime flow:**
1. POST to token endpoint with `client_id`, `client_secret`, `grant_type=client_credentials`, `scope`
2. Receive `access_token` (bearer JWT or opaque), `expires_in`, optionally `refresh_token`
3. Use bearer token in `Authorization: Bearer {token}` header on subsequent API calls
4. Refresh proactively at ~80% of TTL; fall back to retry-once-on-401 if refresh fails

**Strength:** Medium. Shared secret traverses the network at every token exchange. Safe over TLS, but secret rotation matters.

**Critical implementation notes:**
- Cache tokens. Never re-exchange on every API call.
- Cache key in EdTech is `(partner, lea_id)` not just `(partner)` — each district gets its own token scoped to its data.
- Rotate `client_secret` annually or on suspected compromise.


## OAuth 2.0 Client Credentials with JWT client assertion (private_key_jwt)

Higher-security variant. The client signs a JWT with its private key instead of sending a shared secret. The server verifies with the registered public key.

**Where it's used in EdTech:**
- **LTI 1.3 service calls — mandatory.** No LTI 1.3 platform accepts shared-secret client auth.
- Some IEP vendors that have a higher security bar.
- Some direct SIS integrations (PowerSchool plugin API in some configurations).

**Onboarding artifacts exchanged:**
- `client_id`
- Your **public key** (you generate the keypair; you send only the public half) or a **JWKS URL** the server fetches from
- token endpoint URL
- scopes

**Runtime flow:**
1. Construct a JWT with claims: `iss` (your client_id), `sub` (your client_id), `aud` (the token endpoint), `jti` (random unique ID for replay protection), `exp` (short, ~5 min)
2. Sign the JWT with your private key (RS256 or ES256)
3. POST to token endpoint with `client_assertion_type=urn:ietf:params:oauth:client-assertion-type:jwt-bearer`, `client_assertion=<the JWT>`, `grant_type=client_credentials`
4. Receive `access_token`
5. Use bearer token in API calls

**Strength:** High. No shared secret ever traverses the network. Private key never leaves your servers. Cryptographic non-repudiation.

**This is the established pattern for high-security B2B auth in regulated integrations.**

**Critical implementation notes:**
- Keep `jti` unique and `exp` short (~5 min) to prevent replay attacks.
- Rotate keypairs annually. JWKS URL approach makes rotation much easier than direct public key exchange.
- Cache tokens like with `client_secret` — token caching is independent of how you authenticated to get the token.

## HMAC-SHA256 request signing

Used for **inbound webhook verification.** Not a primary client auth pattern — it authenticates individual requests, not sessions.

**Where it's used in EdTech:**
- Clever webhooks (`Clever-Signature` header)
- ClassLink webhooks
- EdLink webhooks
- Ednition webhooks (verify)
- Most modern partner webhook deliveries

**Onboarding artifacts exchanged:**
- Shared secret (you generate or the partner generates; both sides hold)
- The hash algorithm (almost always HMAC-SHA256)
- Your webhook receiver URL (you give this to the partner)

**Runtime flow on receive:**
1. Partner sends POST to your webhook URL with JSON body
2. Partner includes a header like `X-Signature: t=<timestamp>,v1=<hex-signature>`
3. You construct the signing string: `<timestamp>.<raw_request_body>` (exact format depends on partner spec)
4. You compute `HMAC-SHA256(shared_secret, signing_string)` and compare to the header value
5. You check the timestamp is within a replay window (typically 5 min)
6. You check the event ID has not been processed before (idempotency)
7. Only after all three checks do you process the webhook body

**Strength:** Medium for the use case. Symmetric secret, but scoped per-request and bounded by a freshness window.

**Critical implementation notes:**
- Always verify signature **before** parsing the body in your application logic. Verification operates on the raw bytes.
- Use constant-time comparison (`hmac.compare_digest` in Python) to prevent timing attacks.
- Reject requests with a stale timestamp.
- Persist `event_id` for idempotency on processing. A replayed event within the freshness window is still legitimate (network retry) but must not be processed twice.


## mTLS — mutual TLS

Network-level peer authentication. Both client and server present X.509 certificates.

**Where it's used in EdTech:**
- Some state-run IEP systems (SEIS, CT-SEDS) require mTLS layered with SFTP
- Some state-level Ed-Fi feed deliveries
- Rare in mainstream EdTech but present in the state-government corner of the IEP lane

**Onboarding artifacts exchanged:**
- Your client certificate (issued by a CA the server trusts, or a self-signed cert pinned by the server)
- The server's certificate (so you can verify their identity)

**Runtime flow:** Handled at the TLS handshake level. By the time your application code runs, peer identity is established.

**Critical implementation notes:**
- Certificate rotation is operationally expensive. Build automation for it from day one.
- The hardest part of mTLS is usually getting the cert issued. Some states require business-associate agreements or state-approved-vendor status first.

## SFTP — SSH File Transfer Protocol

File-based exchange over SSH-authenticated channel.

**Where it's used in EdTech:**
- State-run IEP systems for batch data exchange
- Older Skyward deployments
- Ed-Fi state-level feeds
- Some assessment platforms for large data dumps

**Onboarding artifacts exchanged:**
- Your SSH public key (or partner-issued credentials)
- SFTP host, port, directory structure
- File format spec (CSV, fixed-width, OneRoster CSV, Ed-Fi CSV)

**Runtime flow:**
1. Connect via SFTP using key-based auth
2. Drop files in the agreed inbox directory (outbound) or poll the agreed outbox directory (inbound)
3. Process files according to the format spec
4. Move processed files to an archive directory; alert on parse failures

**Critical implementation notes:**
- File-based exchange is fundamentally async and high-latency. Don't model it as "real-time."
- Always retain raw files for audit and replay. FERPA may require this.
- Idempotency keyed on filename + content hash. Don't process the same file twice.


## OIDC — OpenID Connect

Identity layer on top of OAuth 2.0. ID token (a JWT) carries user identity claims.

**Where it's used in EdTech:**
- LTI 1.3 launch handshake (the moment a teacher clicks "Open" on the tool inside Canvas)
- SSO via Clever, Google, Microsoft, ClassLink
- Any "Sign in with X" flow for end-user authentication

**Different from the others in this doc:** OIDC authenticates a user, not a service. The other patterns above are service-to-service. OIDC is service-to-user.

**Critical implementation notes:**
- LTI 1.3 launch is the most common OIDC use case in EdTech. Read the LTI 1.3 spec when you need to implement it.
- ID token signature verification uses the platform's JWKS endpoint, same pattern as JWT client assertion in reverse.

## Pattern selection matrix

| Integration partner | Outbound auth | Inbound webhook auth | Notes |
|---|---|---|---|
| Clever | OAuth 2.0 client_secret | HMAC-SHA256 | |
| ClassLink | OAuth 2.0 client_secret | HMAC-SHA256 | OneRoster underneath |
| EdLink | OAuth 2.0 client_secret | HMAC-SHA256 (verify) | |
| Ednition (RosterStream) | OAuth 2.0 client_secret (verify) | HMAC-SHA256 (verify) | MCP API for agentic access |
| Direct PowerSchool API | OAuth 2.0 client_secret | n/a | |
| Direct Infinite Campus | OAuth 2.0 client_secret | n/a | |
| Direct Skyward | OAuth 2.0 client_secret or SFTP | n/a | depends on version |
| Frontline IEP | OAuth 2.0 client_secret (verify) | depends | per-partner agreement |
| PowerSchool Special Programs | OAuth 2.0 piggybacked on PowerSchool SIS | n/a | |
| SEIS (California) | SFTP + SSH keys, possibly mTLS | n/a | state-approved vendor agreement required |
| CT-SEDS | SFTP + SSH keys | n/a | state-approved vendor agreement required |
| Canvas / Schoology / Brightspace / Moodle | LTI 1.3 with JWT assertion | LTI service-call inbound | mandatory pattern |
| Google Classroom | OAuth 2.0 (user or service account) | n/a | |
| NWEA MAP, iReady, Renaissance | OAuth 2.0 client_secret per vendor | varies | rostering via aggregator |
| Ed-Fi state feeds | SFTP + mTLS | n/a | state-by-state agreements |

Items marked as unverified need confirmation against partner documentation before implementation.
