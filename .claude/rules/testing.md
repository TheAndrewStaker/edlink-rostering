---
paths:
  - e2e/**/*.{ts,tsx,json}
  - web/**/*.test.{ts,tsx}
  - web/**/__tests__/**
  - web/vitest.config.ts
  - e2e/playwright.config.ts
---

# Frontend testing discipline

How the React UI is tested. Two layers, each with a defined job. Pick the
layer that matches the bug you are trying to catch; do not duplicate.

## The two layers

**Component layer.** Vitest + React Testing Library + MSW (Mock Service
Worker). Lives at `web/src/**/__tests__/*.test.tsx`. Runs on
every save in under a second. Hits a mocked HTTP boundary, not the real
API. Owns:

- Client-side form validation (required field, format, length, disabled
  Confirm until fields valid).
- Optimistic update + rollback on error (cache state assertions).
- Dialog open/close, focus management, accessibility roles.
- Mutation hook contracts (the `useFooMutation` shape).
- Label translation (`labelForPartner`, `labelForSeverity`) when rendered.

**End-to-end layer.** Playwright against the real FastAPI + Postgres
stack started by `playwright.config.ts`. Lives at
`e2e/specs/*.spec.ts`. Runs in CI and on demand. Owns:

- One happy-path spec per mutation pathway, asserting the UI reflects
  authoritative server state after the refetch.
- One server-error spec per mutation pathway, asserting the error toast
  appears and the UI rolled back.
- Routing, auth, persona switching, drawer + page navigation smoke.
- Full-stack regressions where the React contract and the FastAPI
  contract have to agree.

Anything that is neither of these (pure display, hover, sort, filter,
pagination) is not a test target unless it broke in production.

## The mutation-pathway rule

For every mutation in the app, three tests must exist:

| Test | Layer | What it asserts |
|---|---|---|
| Client validation | Component | Confirm button stays disabled until required fields are valid; invalid submit blocked at the form. |
| Server error | Component (rollback) + e2e (toast + rollback) | When the API returns 4xx/5xx, the optimistic cache rolls back, the dialog stays open, and the error toast fires. |
| Happy path | e2e | Click through the dialog, assert the cache + UI reflect server truth after `onSettled` invalidation. |

A "mutation pathway" is one user-visible action that fires a write. Not
one button. A dialog with Authorize, Cancel, and Close-X is one pathway,
not three.

## What gets a Playwright spec

Inventory each mutation in the app and queue two specs (happy + error)
per pathway. Display-only interactions do not get specs.

Current inventory:

- `ConnectorActions` authorize, revoke, rotate, adjust-poll-interval.
  Four pathways.
- `ReasonDialog` quarantine release with reason. One pathway.
- `SendTestEventMenu` happy scenario. The L1 through L5 + drift
  scenarios are the error fixtures; one spec per error layer covers them.
- `DevPersonaSwitcher` persona switch. One happy spec; no error path.

Display-only that does not get a spec:

- Table sort, filter, search.
- Drawer open/close.
- KPI strip rendering.
- Audit-log pagination scroll.

## Test file location and naming

```
e2e/
├── package.json              # @playwright/test only
├── playwright.config.ts      # baseURL, webServer, trace, video
├── fixtures/
│   ├── test-base.ts          # extended `test` with db fixture
│   └── auth.ts               # mint JWT, set persona
├── specs/
│   ├── connectors.spec.ts    # one file per page or feature
│   ├── quarantine.spec.ts
│   └── dev-test-events.spec.ts
└── tsconfig.json
web/
└── src/
    └── components/
        └── __tests__/
            ├── ConnectorActions.test.tsx
            ├── ReasonDialog.test.tsx
            └── ...
```

One spec file per page or feature. Not "all dialogs in one file." Not
"all mutations in one file." When a spec file passes ~300 LOC, split by
feature.

## Selector discipline

In order of preference:

1. `getByRole("button", { name: "Authorize" })` for actionable elements.
2. `getByLabel("Reason")` for form fields.
3. `getByText("...")` for non-interactive copy assertions.
4. `getByTestId("...")` only when none of the above produces a unique
   match. Add `data-testid` in the component at the same time as the spec;
   do not retrofit later.

Selectors derived from CSS classes, indices, or Chakra-internal markup
are forbidden. They break on every Chakra upgrade.

## Auth in e2e

Auth uses `/api/dev/mint-jwt` against the running API. `auth.ts` fixture
mints a token for the persona the spec needs and writes it into
`storageState.json` so the page loads pre-authenticated. Personas:

- `owner` for cross-LEA actions.
- `admin` for connector lifecycle.
- `support_readonly` for permission negative tests.

Each spec declares the persona it needs in its fixture options; the
fixture sets the JWT in localStorage before the page navigates.

## DB state in e2e

Default policy: per-spec reset via `scripts/dev-reset.sh` +
`seed-dev.sh`. Reset runs in the `db` fixture before the page navigates.

Revisit this policy after the first three specs land. If total suite
runtime stays under 60 seconds, keep per-spec reset. If it grows,
switch to per-spec LEA isolation: one reset at suite start, each spec
creates a scoped LEA (`lea-e2e-<short-uuid>`) and references only that
LEA's data.

## Error path injection

Each mutation pathway needs a deterministic way to make the API fail.
Options in order of preference:

1. **Reuse existing test fixtures.** `SendTestEventMenu` has
   `l1_signature_mismatch`, `l2_schema_missing_field`,
   `l3_parse_invalid_date`, `l4_orphan_enrollment`, `l5_event_volume_spike`,
   `reconciliation_drift`. The error spec triggers one of these and
   asserts the row surfaces under "Quarantine" or "Alerts."
2. **Pre-existing state.** Seed a LEA in a state that makes the action
   fail (revoke a connector that is already revoked → 409).
3. **Test-only error injection endpoint.** New endpoint that registers
   "next call to X fails with status Y for LEA Z." Last resort; the
   surface area is wire-format pollution.

Never mock or intercept network requests inside a Playwright spec. The
point of e2e is the real wire. Mocking belongs in the component layer.

## Assertion checklist per spec

Every spec ends with explicit assertions for these three things:

1. **Server state.** Refetch the relevant query or hit the API directly
   and assert the row has the expected `status`, `last_event_at`,
   `audit_log_id`. Do not stop at "the toast appeared."
2. **UI state.** The element the operator looks at reflects the change.
   Drawer section, table row badge, KPI counter, alert pill.
3. **Audit trail.** A new row exists in the audit log endpoint with the
   operator identity, the action, and the reason.

Skipping any of the three lets a regression through. The audit
assertion is especially load-bearing per `security.md` and
`integration-protocol.md`.

## Scripts and run configs

Per `feedback_intellij_run_configs` and the project CLAUDE.md, every
test command lands as both a `scripts/*.sh` and a matching
`.idea/runConfigurations/*.xml` in the same change.

Required entries when this rule first applies:

- `scripts/e2e.sh` and `Tests__E2E.xml`. Headless run.
- `scripts/e2e-headed.sh` and `Tests__E2E_Headed.xml`. Headed
  + trace on for debugging.
- `scripts/test-web.sh` and `Tests__Web.xml`. Vitest watch.

Each script sources `_lib.sh` to pick up `.env` like the existing dev
scripts.

## What is forbidden

- Skipping a failing test to make a suite green. Fix it or escalate.
- Asserting only on toast text. The toast is the symptom; assert the
  state.
- Hard-coded waits (`page.waitForTimeout(2000)`). Use Playwright's
  `expect.toHaveText`, `toBeVisible`, or `toHaveURL` with implicit retry.
- Cross-spec ordering dependencies. Every spec must pass on its own.
- Mocking the network in Playwright. Mock in Vitest, not in e2e.
- One mega-spec that does five things. One pathway per spec.

## Sign-off gates

A feature with a UI mutation is not done until:

- [ ] Vitest component test exists for validation + rollback.
- [ ] Playwright happy-path spec exists and passes.
- [ ] Playwright server-error spec exists and passes.
- [ ] `npx tsc --noEmit` clean in `web/`.
- [ ] `scripts/e2e.sh` clean.

The visual sign-off in `VISUAL_SIGNOFF.md` is still required for the
founder review. The automated tests are the regression net, not a
replacement for visual confirmation.

## Cross-references

- `.claude/rules/frontend-mutations.md` — mutation hook patterns the
  tests verify.
- `.claude/rules/frontend-forms.md` — form patterns and validation.
- `.claude/rules/frontend-copy.md` — label translation, the tests
  assert rendered prose not enum codes.
- `.claude/rules/scripts.md` — script + run-config pairing rule.
- `.claude/rules/integration-protocol.md` — idempotency and audit
  contract the e2e specs verify.
