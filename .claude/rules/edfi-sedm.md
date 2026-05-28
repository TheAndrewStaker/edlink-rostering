---
paths:
  - api/src/edlink_rostering/connectors/edfi/**/*.py
  - api/src/edlink_rostering/canonical/iep/**/*.py
  - api/src/edlink_rostering/canonical/sedm/**/*.py
  - api/src/edlink_rostering/compliance/idea*.py
---

# Ed-Fi and SEDM discipline

Rules when working with Ed-Fi-shaped data, with special focus on the Special Education Data Model (SEDM) for IEP work.

Reference: Ed-Fi 6.1 docs at https://docs.ed-fi.org/reference/data-exchange/data-standard/whats-new/whats-new-v61/ and SEDM at https://datastandardsunited.org/ceds-sedm.

## Read the spec first

Ed-Fi has hundreds of entities. **Before designing against an Ed-Fi resource, read its handbook entry.** Vendor implementations vary in which fields are populated; the handbook is the canonical reference.

URLs:

- Ed-Fi Data Standard 6.1: https://docs.ed-fi.org/reference/data-exchange/data-standard/whats-new/whats-new-v61/
- SEDM section in 6.1: in the same release notes
- Ed-Fi ODS/API live docs: https://api.ed-fi.org/v7.1/docs/swagger (and equivalents per version)

## Target version is 6.1 (with SEDM)

Design canonical against Data Standard 6.1. **SEDM is the IEP data model** the application normalizes to, even though it's currently early access.

When a district is on an older Data Standard (4.x, 5.x), the connector handles the version mismatch by mapping older fields to 6.1 canonical shape. **Don't store 5.x-shaped data in the application.**

## Descriptor URIs

Ed-Fi uses URI-typed enumerated values:

```
uri://ed-fi.org/GradeLevelDescriptor#Ninth grade
uri://ed-fi.org/DisabilityDescriptor#SpecificLearningDisability
uri://ed-fi.org/ServiceTypeDescriptor#SpeechTherapy
```

When ingesting:

1. **Don't store the full URI as-is in canonical.** Strip the namespace, store the value as an enum.
2. **Validate the value against the descriptor catalog for the data standard version.**
3. **Handle state extensions.** Texas TEA's TSDS layers `uri://tea.texas.gov/...` descriptors on top of core Ed-Fi. Be prepared for namespaces you don't recognize.
4. **Preserve the source descriptor in `external_ids`** for round-trip fidelity.

```python
def canonical_disability_from_edfi(descriptor_uri: str) -> tuple[DisabilityCategory, dict]:
    """Map Ed-Fi DisabilityDescriptor to canonical category.

    Returns (category, metadata) where metadata preserves source URI.
    """
    ns, value = parse_descriptor_uri(descriptor_uri)
    canonical = DISABILITY_DESCRIPTOR_MAP.get((ns, value))
    if canonical is None:
        log.warning("unknown_disability_descriptor", uri=descriptor_uri)
        canonical = DisabilityCategory.UNKNOWN

    return canonical, {"source_descriptor_uri": descriptor_uri}
```

The map is per-state extensible.

## Natural keys, not UUIDs

Ed-Fi resources are identified by natural keys that combine multiple fields. A Section's natural key is `(localCourseCode, schoolId, schoolYear, sectionIdentifier, sessionName)`. A StudentSectionAssociation's natural key includes the section's full natural key plus the student's `studentUniqueId`.

**Resolve to canonical IDs at ingest. Don't carry Ed-Fi natural keys around in canonical entities.** Store them in `external_ids` for reverse-lookup.

```python
external_ids = {
    "edfi_section_natural_key": json.dumps({
        "localCourseCode": "BIO101",
        "schoolId": 255901001,
        "schoolYear": 2026,
        "sectionIdentifier": "BIO101-3-FALL2025",
        "sessionName": "FALL2025",
    })
}
```

## School year is the spring year

Ed-Fi uses the spring year as the school year. `schoolYear: 2026` means the 2025-2026 school year.

**Confusing this is a common bug.** When converting:

- Canonical `Term` has `school_year_start` (e.g., 2025) and `school_year_end` (e.g., 2026)
- Ed-Fi `schoolYear` maps to `school_year_end`

Document this conversion in the connector. Test cases that span the calendar year boundary.

## Reference resolution

Ed-Fi resources reference each other by natural key:

```json
{
  "studentReference": { "studentUniqueId": "OK20260512345" },
  "sectionReference": {
    "localCourseCode": "BIO101",
    "schoolId": 255901001,
    "schoolYear": 2026,
    "sectionIdentifier": "BIO101-3-FALL2025",
    "sessionName": "FALL2025"
  }
}
```

The connector resolves these references against canonical entities at ingest. If the referenced entity hasn't been ingested yet (out-of-order arrival), the connector either:

1. Buffers the dependent record and retries when the dependency arrives
2. Pulls the dependency on-demand from the Ed-Fi ODS

Pulling on-demand is simpler but increases call volume. Buffering is more efficient but requires careful idempotency on retry.

## Authentication

Ed-Fi ODS/API uses OAuth 2.0 client_credentials. **Per-district credentials.**

Tokens are typically short-lived (1 hour). Cache and refresh per `.claude/rules/integration-protocol.md`.

Claim sets matter: a token might have access to some resources but not others. Handle `403 Forbidden` gracefully when claim set is narrower than expected.

## Ed-Fi OneRoster Service: when source is Ed-Fi, prefer OneRoster vocabulary

If a district has Ed-Fi ODS 7.3.2+ with the OneRoster Service deployed, consume OneRoster 1.2 from the bridge rather than raw Ed-Fi resources. Reasoning:

1. Simpler vocabulary (OneRoster has ~10 entities vs Ed-Fi's hundreds)
2. Same underlying data
3. The connector code is reusable across Ed-Fi-backed and non-Ed-Fi OneRoster sources
4. 1EdTech conformance certification target is OneRoster, not Ed-Fi consumer

**Exception:** SEDM data. The OneRoster bridge doesn't expose IEP data. For IEP work, go direct to Ed-Fi SEDM endpoints.

## SEDM is early access — handle accordingly

SEDM is in early access in Ed-Fi 6.1. Implication for code:

- Schemas may change before stable release
- Production use is supported but documented as early-access in ADRs
- Track RFC 28b (feedback channel) for community discussion
- Be prepared to update mappings as the model stabilizes

When ingesting SEDM data:

1. Tag canonical records with `source_schema_version="sedm-early-access-6.1"` for forward compatibility
2. Don't normalize aggressively — if SEDM has fields the application doesn't currently use, preserve them in `external_ids` or a `raw_payload` overflow field
3. Mark SEDM-derived ADRs as "subject to revision pending SEDM stable release"

## SEDM five entities

| SEDM entity | Canonical entity | Notes |
|---|---|---|
| `studentIEP` | `IEP` | Use temporal-snapshot model per `temporal-model` rule |
| `IEPGoal` | `IEPGoal` | Attached to IEP snapshot |
| `IEPService` | `IEPService` | Service minutes are legally binding — preserve exactly |
| `IDEAEvent` | `IDEAEvent` | Append-only procedural log |
| (related associations) | (canonical relationships) | Inline as foreign keys |

The SEDM early-access spec at https://datastandardsunited.org/ceds-sedm has the field-by-field discussion.

## IDEA event correlation

IDEA events form a causal chain:

```
ReferralReceived → InitialEvaluation → EligibilityDetermined → InitialIEPMeeting → InitialIEPInEffect
                                                                ↓
                                            (annual reviews)
                                                                ↓
                                            (triennial reevaluation)
```

When ingesting `IDEAEvent` records, the canonical model preserves the chain via causal references. **Don't lose the causal structure** — it's the basis for the timeline math in `.claude/rules/compliance.md`.

## Write-back considerations

If the application writes back to Ed-Fi (e.g., student progress data), the write happens against Ed-Fi-shaped payloads, not canonical. The connector translates outbound.

**For SEDM write-back specifically:** check whether the target Ed-Fi deployment supports SEDM writes. Many state ODS deployments don't yet because SEDM is early access. Fall back to vendor-specific write paths if needed.

## State extensions

Most state Ed-Fi deployments have state-specific extensions. Examples:

- Texas: TSDS extensions for state-specific reporting
- North Carolina: NCDPI extensions
- Indiana: per-IDOE specifications

The connector handles these via:

1. **Per-state extension modules** in `connectors/edfi/state/`
2. **Configuration-driven mapping** for state-specific descriptor catalogs
3. **Graceful unknown handling** — preserve unknown extension data in `raw_payload` rather than dropping

## Profile awareness

Ed-Fi has a concept of "Profiles" — subsets of the data standard for specific use cases. State systems may operate under a specific Profile (e.g., a Title I Profile). The connector should:

1. Read the Profile that the target ODS exposes
2. Validate that needed resources are within the Profile
3. Surface mismatch errors early

## Pagination

Ed-Fi ODS/API uses offset-based pagination, similar to OneRoster. Default limit is typically 100; max is per-deployment (often 500).

```python
async def fetch_students(self, since: datetime | None = None) -> AsyncIterator[Student]:
    offset = 0
    limit = 500
    while True:
        params = {"limit": limit, "offset": offset}
        if since:
            params["minChangeVersion"] = await self.change_version_at(since)

        response = await self.client.get("/students", params=params)
        records = response.json()
        if not records:
            break

        for record in records:
            yield canonical_student_from_edfi(record, self.lea_id)

        offset += limit
```

`minChangeVersion` is Ed-Fi's incremental sync mechanism (more reliable than timestamp-based; uses an opaque integer).

## Change version vs timestamp

Ed-Fi exposes both:

- `dateLastModified` (timestamp)
- Change version (integer, opaque, monotonically increasing)

**Prefer change version for incremental sync.** Timestamps can have timezone drift; change version is internally consistent.

```python
async def incremental_sync(self) -> AsyncIterator[CanonicalEntity]:
    last_seen_change_version = await self.sync_state.last_change_version()
    response = await self.client.get(
        "/students",
        params={"minChangeVersion": last_seen_change_version, "limit": 500},
    )
    ...
    await self.sync_state.update_change_version(max_change_version_seen)
```

## Tests

Heavy on fixtures. Ed-Fi response shapes are intricate.

```python
def test_translate_student_with_disability():
    edfi_student = load_fixture("edfi/student_with_disability.json")
    edfi_designations = load_fixture("edfi/student_special_education_program.json")
    canonical = canonical_student_from_edfi_with_designations(
        edfi_student, edfi_designations, LeaId("d-123")
    )
    assert canonical.has_iep is True
    assert canonical.primary_disability == DisabilityCategory.SpecificLearningDisability
```

State-extension tests are per-state. If the application enters a new state with extensions, add state-specific fixtures.

## Cross-references

- Ed-Fi 6.1 — https://docs.ed-fi.org/reference/data-exchange/data-standard/whats-new/whats-new-v61/
- SEDM — https://datastandardsunited.org/ceds-sedm
- `docs/standards/STANDARDS_COVERAGE_MATRIX.md` — what the application supports and plans
- `.claude/rules/integration-protocol.md` — connector contract
- `.claude/rules/temporal-model.md` — IEP snapshot pattern
- `.claude/rules/compliance.md` — where IDEA timeline math lives
- `.claude/rules/oneroster.md` — when source is Ed-Fi but consuming OneRoster vocabulary
