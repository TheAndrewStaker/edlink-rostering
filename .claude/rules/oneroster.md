---
paths:
  - api/src/edlink_rostering/connectors/oneroster/**/*.py
  - api/src/edlink_rostering/connectors/ednition/**/*.py
  - api/src/edlink_rostering/connectors/edlink/**/*.py
  - api/src/edlink_rostering/connectors/clever/**/*.py
  - api/src/edlink_rostering/connectors/classlink/**/*.py
---

# OneRoster discipline

Rules when working with OneRoster-shaped data, whether sourced directly or via an aggregator (Ednition, EdLink, Clever, ClassLink).

Reference: the OneRoster 1.2 spec at https://www.imsglobal.org/spec/oneroster/v1p2 is authoritative.

## Read the spec first

Before designing any feature that depends on a OneRoster field, **read the relevant section of OneRoster 1.2.** Vendor docs (Ednition, EdLink) are sometimes inconsistent with the spec; the spec is the source of truth.

Spec URL: https://www.imsglobal.org/spec/oneroster/v1p2

## Target version is 1.2

Design against 1.2. 1.1 is deprecated by 1EdTech and most production OneRoster sources have migrated. Older 1.0 is not supported anywhere current.

If a partner only exposes 1.1, the connector should:

1. Map 1.1 fields to 1.2 canonical (which is what the application uses internally)
2. Synthesize the 1.2-only fields (preferredFirstName, multi-role) as empty/single-role
3. Log the version mismatch for visibility

```python
# Good — surface that we're consuming a downlevel version
log.info("oneroster_version_consumed", version="1.1", connector_id=conn.id)
```

## `sourcedId` is opaque

`sourcedId` is defined as an opaque string. **Don't assume UUID, don't assume any particular format.** Some implementations use `db_pk_string`, some use natural keys like `student-12345`, some use vendor-internal IDs.

Treat `sourcedId` as a black-box identifier. Don't parse it. Don't generate canonical IDs from it. Use it only for resolution against `external_ids`.

## `userIds` array is the cross-system identity bridge

A OneRoster user typically has multiple identifiers:

```json
"userIds": [
  { "type": "sis", "identifier": "S98765" },
  { "type": "state", "identifier": "OK20260512345" },
  { "type": "clever", "identifier": "..." },
  { "type": "classlink", "identifier": "..." },
  { "type": "google", "identifier": "alice.smith@district.example" }
]
```

When resolving identity (per `.claude/rules/integration-protocol.md`), try each in priority order:

1. `state` (most stable, cross-district)
2. `sis` (district-scoped stable)
3. Aggregator-specific (`clever`, `classlink`) — fall back, useful for cross-system reconciliation
4. `google`, `microsoft` — useful for SSO matching but can change

Document the priority order in code; don't let it drift implicitly.

## Status semantics

OneRoster has two status values:

- `active` — current record
- `tobedeleted` — flagged for deletion at next sync (treat as inactive)

When ingesting, **filter for `status=active`** unless you specifically need to track deletions. Many vendors don't return `tobedeleted` records in standard queries.

If your connector needs to detect deletions:

1. Use `?filter=status='tobedeleted'` in the query (1.2 syntax)
2. Or fall back to "record present in last sync, absent this sync" diff logic

Don't conflate `tobedeleted` with hard-deleted. Both can happen.

## `dateLastModified` is the cursor

Use `dateLastModified` for incremental sync. **Always present.** Never derive from anything else.

```python
async def incremental_sync(self, since: datetime) -> AsyncIterator[CanonicalUser]:
    page = 0
    while True:
        response = await self.client.get(
            "/users",
            params={
                "filter": f"dateLastModified>'{since.isoformat()}'",
                "limit": 1000,
                "offset": page * 1000,
                "sort": "dateLastModified",
            },
        )
        users = response["users"]
        if not users:
            break
        for user in users:
            yield canonical_user_from_oneroster(user, self.lea_id)
        page += 1
```

Watch for vendor variations in date format and timezone (some use UTC, some local time with explicit offset). The connector normalizes to UTC.

## Pagination

OneRoster pagination is offset-based: `?limit=N&offset=M`. Most implementations cap `limit` at 1000 or so. The connector handles pagination internally; consumers see an iterator.

Always include `sort` to ensure stable pagination. `sort=sourcedId` or `sort=dateLastModified` are common safe choices.

## Filtering syntax

OneRoster 1.2 supports a constrained query language for filters:

```
?filter=status='active'
?filter=role='student'
?filter=status='active' AND grades CONTAINS '09'
?filter=dateLastModified>'2026-05-01T00:00:00.000Z'
```

Quotes around string values. Date literals in ISO 8601 in single quotes. The supported operators are documented in the spec.

Vendor implementations vary in what they support. The connector tests cover what each vendor accepts.

## Multi-role users (1.2 only)

A user can hold multiple roles. Use the `roles` array, not the deprecated `role` field:

```json
"roles": [
  {
    "roleType": "primary",
    "role": "student",
    "org": { "sourcedId": "school-100", "type": "org" },
    "beginDate": "2025-08-15",
    "endDate": "2026-06-05"
  },
  {
    "roleType": "secondary",
    "role": "student",
    "org": { "sourcedId": "school-200", "type": "org" },
    "beginDate": "2025-08-15",
    "endDate": "2026-06-05"
  }
]
```

Canonical translation produces one or many canonical role records per user. Don't collapse multi-role users into single-role canonical entities.

## Date conventions on `beginDate` and `endDate`

OneRoster spec doesn't explicitly mandate inclusive vs exclusive. **Treat as inclusive on both ends until verified otherwise.** This matches the convention in `.claude/rules/temporal-model.md`.

Document the convention in your canonical model and don't rely on partner-specific behavior. When in doubt, check partner sandbox data with known-effective dates.

## CSV vs REST

Some districts provide OneRoster via CSV (uploaded to SFTP or aggregator portal). Same data model, different transport.

CSV-mode considerations:

- Snapshot, not stream — full file each sync
- No `dateLastModified` cursor at the record level; the file's upload timestamp is the closest you get
- Diff computation happens in the application (or aggregator handles it)
- Validate `manifest.csv` first — it declares which other files are present
- Quote and escape characters per RFC 4180 strict

For CSV-only sources, full diff every sync. Cheaper to delegate this to the aggregator.

## Gradebook service is separate

The Gradebook service in OneRoster is a different API surface from Rostering. Don't conflate. If the application supports grade passback to LMSes, it goes through LTI 1.3 AGS or Clever LMS Connect — not OneRoster Gradebook (which is for SIS gradebook, less commonly implemented).

## Bulk service is rare

OneRoster's bulk service is optional and most implementations don't expose it. Plan for paginated REST as the default.

## Vendor variations to watch

| Vendor | Common variation |
|---|---|
| Ednition | Standards-compliant; their AI mapping (RosterAI) handles SIS quirks before exposing OneRoster |
| EdLink | Exposes OneRoster as one of multiple Unified API outputs; their Graph and User Data APIs are more flexible |
| Clever | Uses its own API by default; OneRoster export is available but less common |
| ClassLink | Roster Server exports OneRoster CSV |
| Direct from Ed-Fi ODS | Via Ed-Fi OneRoster Service (new in 2026); cert-pending |
| PowerSchool | OneRoster certified; vendor-direct option |
| Infinite Campus | OneRoster certified; vendor-direct option |

## What goes into canonical, what stays at the connector

Canonical entities:

- `User`, `Student`, `Teacher` (with role-derived subtypes)
- `Org` (district or school)
- `Class`
- `Course`
- `Enrollment`
- `Term`

The connector knows OneRoster. Above the connector, only canonical types exist.

`metadata` extension properties from OneRoster are NOT promoted to canonical fields by default. If a `metadata` value carries a application-relevant field (rare), add an explicit canonical field and map it; don't leak the partner's extension shape into canonical.

## Tests

Unit-test the translation with realistic OneRoster fixtures:

```python
def test_translate_user_with_multiple_roles():
    or_user = load_fixture("oneroster/user_multi_role.json")
    canonical = canonical_user_from_oneroster(or_user, LeaId("d-123"))
    assert len(canonical.roles) == 2
    assert canonical.preferred_first_name == "Ali"
    assert "state" in canonical.external_ids
```

Component-test with mocked HTTP (`respx`).

Sandbox-test against the actual partner if possible.

## Cross-references

- OneRoster 1.2 spec — https://www.imsglobal.org/spec/oneroster/v1p2
- `docs/standards/oneroster-mvp-fields.md` — MVP field cheat-sheet
- `docs/standards/STANDARDS_COVERAGE_MATRIX.md` — what the application supports and plans
- `docs/partners/ednition.md`, `docs/partners/edlink.md` — vendor-specific notes
- `.claude/rules/integration-protocol.md` — connector contract
- `.claude/rules/multi-tenancy.md` — lea_id discipline (sourcedId is district-scoped)
