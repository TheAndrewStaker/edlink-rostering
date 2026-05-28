# Standards coverage matrix

What standards and sub-features the rostering framework currently supports. Used for procurement RFP responses, vendor comparisons, and roadmap planning.

Update this doc whenever scope expands. The current state column reflects what's actually shipped, not what's planned.

## Summary

| Standard | Version | Scope | Status |
|---|---|---|---|
| OneRoster | 1.2 | Rostering REST consumption | Planned |
| OneRoster | 1.2 | Rostering CSV consumption | Planned (via Ednition/EdLink) |
| OneRoster | 1.2 | Gradebook service | Future |
| OneRoster | 1.2 | Assessment Results Profile | Future |
| Ed-Fi Data Standard | 6.1 | Consumer of Ed-Fi 6.1 resources | Future |
| Ed-Fi Data Standard | 5.2 | Consumer of Ed-Fi 5.2 resources | Future |
| Ed-Fi SEDM | Early access | IEP / IDEA event consumption | Future, post-MVP |
| LTI 1.3 with Advantage | Stable | Tool role for LMS launch (NRPS + AGS) | Future |
| CEDS | n/a | Glossary alignment only | Reference only |
| 1EdTech Conformance | OneRoster 1.2 | Service Consumer certification | Future |

## OneRoster 1.2 coverage

### Rostering service (REST consumption)

Inbound consumption from partners (Ednition, EdLink, Clever, ClassLink, direct SIS via Ed-Fi OneRoster Service).

| Endpoint | Status | Notes |
|---|---|---|
| `GET /users` | Planned | Filtered to `status=active` by default |
| `GET /users/{id}` | Planned | |
| `GET /students` | Planned | Convenience for student-role filter |
| `GET /teachers` | Planned | Convenience for teacher-role filter |
| `GET /orgs` | Planned | District + school resolution |
| `GET /schools` | Planned | |
| `GET /classes` | Planned | |
| `GET /courses` | Planned | |
| `GET /enrollments` | Planned | |
| `GET /academicSessions` | Planned | Terms + grading periods |
| `GET /demographics` | Future | Scoped access; not in MVP |
| Bulk service | Not implemented | Use paginated REST instead |

### Rostering service (CSV consumption)

Inbound CSV via SFTP or aggregator-managed exchange.

| File type | Status | Notes |
|---|---|---|
| `users.csv` | Planned (via aggregator) | Direct CSV not in MVP |
| `orgs.csv` | Planned (via aggregator) | |
| `classes.csv` | Planned (via aggregator) | |
| `enrollments.csv` | Planned (via aggregator) | |
| `academicSessions.csv` | Planned (via aggregator) | |
| `demographics.csv` | Future | |
| `courses.csv` | Planned (via aggregator) | |
| `manifest.csv` | Planned (via aggregator) | |

### Gradebook service

| Function | Status | Notes |
|---|---|---|
| Line items (CRUD) | Future | Tied to LTI AGS work |
| Categories | Future | |
| Results (push) | Future | |

### Assessment Results Profile

Out-of-class assessment results.

| Function | Status |
|---|---|
| Assessment listing | Future |
| Score push | Future |

## Ed-Fi Data Standard coverage

Consumption only. The application is not an Ed-Fi ODS provider.

### Ed-Fi 6.1 / 5.2 (core)

| Resource | Status | Notes |
|---|---|---|
| `EducationOrganization` (LEA, School) | Future | Via Ed-Fi OneRoster Service preferred |
| `Student` | Future | |
| `Staff` | Future | |
| `StudentSchoolAssociation` | Future | |
| `StudentSectionAssociation` | Future | |
| `Section` | Future | |
| `Course` | Future | |
| `Session` | Future | |
| `GradingPeriod` | Future | |
| `Assessment` | Future, post-MVP | |
| `StudentAssessment` | Future, post-MVP | |
| `Attendance*` | Future, Extended Data | |
| `Behavior*` | Future, Extended Data | |

### Ed-Fi SEDM (early access)

Five new entities. Highest priority post-MVP.

| Entity | Status | Notes |
|---|---|---|
| `studentIEP` | Future, post-MVP | The IEP "contract" |
| `IEPGoal` | Future, post-MVP | Annual goals |
| `IEPService` | Future, post-MVP | Prescribed services |
| `IDEAEvent` | Future, post-MVP | Procedural lifecycle log |
| Related associations | Future, post-MVP | |

## LTI 1.3 with Advantage coverage

### Tool role (application launches inside LMS)

| Capability | Status | Notes |
|---|---|---|
| Resource link launch (1.3 Core) | Future | iframe launch from LMS |
| OIDC login initiation | Future | |
| JWT id_token signature verification | Future | |
| JWKS endpoint exposure | Future | |
| Names and Roles Provisioning (NRPS) | Future | Class roster fetch |
| Assignment and Grade Services (AGS) | Future | Grade passback |
| Deep Linking 2.0 | Probably not | Not core to the application's value prop |
| Dynamic Registration | Future | Manual registration first |

### Platform role

The application is unlikely to be a Platform (it doesn't host an LMS). N/A.

## Authentication coverage

| Mechanism | Outbound | Inbound | Notes |
|---|---|---|---|
| OAuth 2.0 client_credentials (client_secret) | Planned | n/a | Default for partner APIs |
| OAuth 2.0 private_key_jwt | Planned for LTI | n/a | LTI 1.3 service calls; high-security partners |
| HMAC-SHA256 (webhook verification) | n/a | Planned | All inbound webhooks |
| mTLS | Planned for state-system SFTP | n/a | Per-state vendor agreements |
| SFTP + SSH keys | Planned | n/a | State IEP systems, legacy file-transfer partners |
| OIDC (user auth) | n/a | Future via LTI | LTI 1.3 OIDC launch handshake |

## Identity provider coverage (SSO)

| Provider | Status | Path |
|---|---|---|
| Clever SSO | Future via aggregator | Ednition SSO Connect or direct |
| ClassLink | Future via aggregator | Ednition SSO Connect or direct |
| Google Workspace | Future | OIDC direct or via aggregator |
| Microsoft Entra ID | Future | OIDC direct or via aggregator |
| SAML | Future | Via aggregator preferred |
| LDAP | Future, on demand | Via aggregator only |

## What the framework does NOT cover (and likely won't)

- **Caliper Analytics** — learning event analytics standard. Out of current scope.
- **CASE** — competency exchange. Useful for standards-based grading; future.
- **QTI** — assessment item format. Not in scope.
- **OpenBadges / CLR** — credential standards. Not in scope.
- **Common Cartridge / Thin Common Cartridge** — content packaging. Not in scope.
- **SCORM / xAPI** — older content interoperability standards. Not in scope.
- **SIF (Schools Interoperability Framework)** — legacy; superseded by OneRoster. Skip.

## RFP response language

When responding to a district RFP that asks about standards support, here's the recommended template:

> The application supports the following standards for K-12 interoperability:
>
> - **OneRoster 1.2** (1EdTech) — REST consumption for rostering. We integrate with district SIS systems via OneRoster 1.2 either directly or through certified aggregator partners (Ednition RosterStream, EdLink).
> - **LTI 1.3 with Advantage** (1EdTech) — Tool role for launching inside Canvas, Schoology, Brightspace, Moodle, and Blackboard. Names and Roles + Assignment and Grade Services supported.
> - **Ed-Fi SEDM** (Ed-Fi Alliance, early access in DS 6.1) — Special Education Data Model for IEP and IDEA event integration with district IEP systems where supported.
>
> Our integration approach is grounded in open standards to ensure no vendor lock-in and to interoperate with the systems districts already operate.

Adjust based on actual shipped scope at time of response.

## Conformance certification roadmap

Pursuing 1EdTech conformance certifications helps in district procurement (gets the application into the official 1EdTech conformance chart at imscert.org).

Order of pursuit:

1. **OneRoster 1.2 Rostering Service Consumer** — once consumption is solid, pursue cert
2. **LTI 1.3 with Advantage Complete (Tool role)** — once NRPS + AGS work
3. **Ed-Fi vendor certification** — when Ed-Fi consumption is meaningful

Conformance is per-version and per-role. Recertify on major version updates.

## References

- 1EdTech conformance chart: http://www.imscert.org
- OneRoster conformance docs: https://www.imsglobal.org/spec/oneroster/v1p2/cert
- Ed-Fi vendor certification: https://www.ed-fi.org/certification/
- LTI conformance: https://site.imsglobal.org/certifications
