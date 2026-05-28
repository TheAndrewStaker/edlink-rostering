# OneRoster 1.2 fields for the rostering MVP

The minimum set of OneRoster 1.2 fields needed for the rostering MVP. Use as a cheat-sheet next to the full spec at https://www.imsglobal.org/spec/oneroster/v1p2.

Scope: **rostering service only.** Gradebook, Resources, and Assessment Results are post-MVP.

Status enum mapping, date semantics, and `userIds` precedence are normative for MVP; everything not listed below is either optional or post-MVP.

## Required per entity

### `user` (mapped to canonical `Student` or `Teacher`)

| OneRoster field | Required? | Canonical mapping | Notes |
|---|---|---|---|
| `sourcedId` | yes | `external_ids["oneroster_sourcedid"]` | Opaque; never assume format |
| `status` | yes | `Student.status` | Filter for `active` per status semantics below |
| `dateLastModified` | yes | `last_modified` | Cursor for incremental sync |
| `givenName` | yes | `given_name` | |
| `familyName` | yes | `family_name` | |
| `roles[]` | yes | derive Student vs Teacher | Use multi-role array, not deprecated `role` |
| `userIds[]` | yes (≥1) | `external_ids` (priority per ADR-003) | `state` > `sis` > aggregator-specific |
| `primaryOrg` | yes | `Student.primary_school_id` (resolve via sourcedId) | |
| `email` | optional | `Student.email` | nullable in MVP |
| `preferredFirstName` | optional | `Student.preferred_first_name` | OneRoster 1.2 only |
| `username` | optional | not stored | Auth concern, not canonical |
| `grades[]` | yes for students | `Student.grade_level` | Use first element; multi-grade is an edge case |
| `agents[]` | post-MVP | not in MVP canonical | Parent/guardian linkage; deferred |
| `enabledUser` | optional | filter behavior | `false` treats as inactive |
| `birthDate` | optional / scoped | `Student.birth_date` | May be omitted by Sharing Rules; FERPA-sensitive |
| `metadata` | post-MVP | not promoted | Vendor extensions; ignore for MVP |

### `org` (mapped to canonical `Lea` or `School`)

| OneRoster field | Required? | Canonical mapping | Notes |
|---|---|---|---|
| `sourcedId` | yes | `external_ids["oneroster_sourcedid"]` | |
| `status` | yes | filter for `active` | |
| `dateLastModified` | yes | `last_modified` | |
| `name` | yes | `name` | |
| `type` | yes | discriminator | `district` (or `lea`) → `Lea`; `school` → `School` |
| `identifier` | optional | `nces_school_id` or `nces_lea_id` | When present, use for cross-system anchor |
| `parent` | yes for school | School's parent district reference | |
| `children[]` | optional | not stored | District's schools resolved via reverse lookup |

### `class`

| OneRoster field | Required? | Canonical mapping | Notes |
|---|---|---|---|
| `sourcedId` | yes | `external_ids["oneroster_sourcedid"]` | |
| `status` | yes | filter for `active` | |
| `dateLastModified` | yes | `last_modified` | |
| `title` | yes | `title` | |
| `classCode` | yes | `class_code` | |
| `classType` | yes | discriminator | `scheduled` is MVP; `homeroom` is post-MVP |
| `school` | yes | `School.id` (resolve via sourcedId) | |
| `course` | yes | `Course.id` (resolve via sourcedId) | |
| `terms[]` | yes (≥1) | `Term.id` (resolve via sourcedId) | |
| `grades[]` | optional | not stored as primary | School-level grade context |
| `subjects[]` | optional | `subject` | First element used |
| `periods[]` | optional | `period` | First element used |
| `location` | optional | `location` | |

### `enrollment`

| OneRoster field | Required? | Canonical mapping | Notes |
|---|---|---|---|
| `sourcedId` | yes | `external_ids["oneroster_sourcedid"]` | |
| `status` | yes | `Enrollment.status` | Inactive enrollments still imported, marked inactive |
| `dateLastModified` | yes | `last_modified` | |
| `role` | yes | `Enrollment.user_role` | `student` or `teacher` |
| `primary` | optional | `Enrollment.primary` | Teacher-only meaningful; default true |
| `beginDate` | yes | `Enrollment.begin_date` | Inclusive per ADR-005 |
| `endDate` | optional | `Enrollment.end_date` | Inclusive; NULL = open per ADR-005 |
| `user` | yes | `Enrollment.user_id` (resolve via sourcedId) | |
| `class` | yes | `Enrollment.class_id` (resolve via sourcedId) | |

### `academicSession` (mapped to canonical `Term`)

| OneRoster field | Required? | Canonical mapping | Notes |
|---|---|---|---|
| `sourcedId` | yes | `external_ids["oneroster_sourcedid"]` | |
| `status` | yes | filter for `active` | |
| `dateLastModified` | yes | `last_modified` | |
| `title` | yes | `name` | |
| `type` | yes | `term_type` | `schoolYear`, `semester`, `term`, `gradingPeriod` |
| `schoolYear` | yes | `school_year_end` | Spring-year form (e.g., 2026 = 2025-2026) |
| `startDate` | yes | `begin_date` | Inclusive |
| `endDate` | yes | `end_date` | Inclusive |
| `parent` | optional | `parent_term_id` | For grading periods within semesters |

## Status semantics

Two values matter:

- `active` — current, real record. Process normally.
- `tobedeleted` — flagged for deletion at next sync. Treat as **inactive** in canonical (`status = 'inactive'` or equivalent). Do **not** physically delete from the application until the next reconciliation confirms removal in the source.

Filter incoming queries with `?filter=status='active'` for normal sync. For deletion detection, separately query `?filter=status='tobedeleted'` or rely on diff-based reconciliation.

**Never delete canonical rows on receiving `tobedeleted`.** Mark inactive; physical retention is governed by FERPA retention policy in `.claude/rules/compliance.md`.

## `dateLastModified` cursor

Mandatory on every entity. Use for incremental sync.

```python
async def incremental_sync(self, since: datetime) -> AsyncIterator[CanonicalEntity]:
    response = await client.get(
        "/users",
        params={
            "filter": f"dateLastModified>'{since.isoformat()}'",
            "limit": 1000,
            "offset": 0,
            "sort": "dateLastModified",
        },
    )
```

**Vendor variation:** some sources use UTC, some local time with explicit offset. Always normalize to UTC at the canonical boundary. Trust the explicit timezone marker in the ISO 8601 string; never assume.

## `userIds` precedence

Per ADR-003 (`docs/decisions/adr-003-identity-resolution-priority.md`):

1. `type=state` (most stable, cross-LEA)
2. `type=sis` (LEA-scoped stable)
3. `type=oneroster_sourcedid` (partner-scoped)
4. `type=edfi_student_unique_id` (Ed-Fi natural key)
5. `type=clever`, `type=classlink` (aggregator-specific)
6. `type=google`, `type=microsoft` (federated SSO, can change)

LEA-scoped: the same `oneroster_sourcedid` in two LEAs is two different students. No probabilistic matching in MVP.

## Multi-role users

A single user can hold multiple roles across organizations. Use `roles[]`; never the deprecated `role` singular field.

```json
"roles": [
  {
    "roleType": "primary",
    "role": "student",
    "org": { "sourcedId": "school-100", "type": "org" },
    "beginDate": "2025-08-15",
    "endDate": "2026-06-05"
  }
]
```

Map each role entry to a canonical enrollment-like record, scoped to its `org`. A user with both `teacher` and `parent` roles produces two canonical records (one Teacher, one Person-with-parent-link), not one combined.

## Pagination

OneRoster 1.2 is offset-based. Always include `sort` for stable pagination.

- `limit`: cap at 1000 typically (vendor-specific; some allow more)
- `offset`: increment by `limit`
- `sort`: `dateLastModified` or `sourcedId` are safe choices

Stop when the response returns fewer than `limit` records or an empty array.

## Date conventions (per ADR-005)

`beginDate`, `endDate`, and term `startDate`/`endDate` are all **inclusive on both ends** in canonical. OneRoster doesn't explicitly mandate; ADR-005 makes the choice. Confirm against vendor-specific behavior in sandbox before treating as final.

## What's intentionally NOT in MVP

- **Gradebook service** — `lineItem`, `result`, `category`. Post-MVP.
- **Resources service** — `resource`, `resourceCollection`. Post-MVP.
- **Assessment Results Profile** — `assessmentResult`, hierarchies. Post-MVP; reads from direct assessment vendor APIs instead.
- **CSV transport** — REST only for MVP. CSV is fallback if a specific LEA requires it.
- **`agents[]` (parent/guardian linkage)** — Post-MVP; FERPA disclosure log implications require dedicated design.
- **`metadata` vendor extensions** — Don't depend on them; don't promote to canonical fields.
- **Demographics** — Sharing Rules and FERPA scope decisions; separate ingest path, post-MVP.

## Cross-references

- OneRoster 1.2 spec — https://www.imsglobal.org/spec/oneroster/v1p2
- `.claude/rules/oneroster.md` — implementation discipline
- `docs/decisions/adr-003-identity-resolution-priority.md` — userIds precedence
- `docs/decisions/adr-005-effective-date-rostering.md` — date conventions
- `architecture/data-model.md` — canonical entity definitions
