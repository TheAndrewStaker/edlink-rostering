---
paths:
  - web/src/**/*.tsx
  - web/src/components/**/*.{ts,tsx}
  - web/src/features/**/*.{ts,tsx}
---

# Frontend components

React conventions for the web UI. Stack: React 18+, TypeScript 5+, TanStack Query for server state, Chakra UI v3.

This rule covers component shape, hooks discipline, and the boundary between server state and client state. Forms and mutations have their own rules.

## Functional components only

No class components. Every component is a function. Most components are arrow-function-with-named-export pairs.

```tsx
// Good
export function StudentCard({ student }: StudentCardProps) {
  return <div>{student.givenName} {student.familyName}</div>;
}

// Equivalent and also fine
export const StudentCard = ({ student }: StudentCardProps) => {
  return <div>{student.givenName} {student.familyName}</div>;
};
```

Pick one style per project and stay consistent. Class components are off the table.

## Component props are typed

```tsx
type StudentCardProps = {
  student: Student;
  onEdit?: (studentId: StudentId) => void;
  variant?: "compact" | "full";
};

export function StudentCard({ student, onEdit, variant = "compact" }: StudentCardProps) {
  ...
}
```

Use `type` aliases for props (not interfaces) — they compose better with utility types and don't have declaration merging surprises.

Required props are required (don't use `?` casually). Optional props have sensible defaults at destructuring.

## No PII in component names, IDs, or comments

Per `.claude/rules/security.md`: even in the frontend, don't embed student PII in code:

```tsx
// Bad
const aliceSmithCard = <StudentCard ... />;
// data-testid="student-card-alice-smith"

// Good
const studentCard = <StudentCard student={student} />;
// data-testid={`student-card-${student.id}`}
```

## Separation: features, components, primitives

```
web/src/
├── features/         # feature-scoped components (compose primitives + components)
│   ├── students/
│   │   ├── StudentList.tsx
│   │   ├── StudentDetail.tsx
│   │   ├── useStudentList.ts
│   │   └── ...
│   ├── ieps/
│   │   ├── IEPSummary.tsx
│   │   ├── IEPGoalList.tsx
│   │   └── ...
│   └── compliance/
│       └── DeadlineAlerts.tsx
├── components/       # shared components (reusable across features)
│   ├── DataTable.tsx
│   ├── EntityHeader.tsx
│   ├── EmptyState.tsx
│   └── ...
└── primitives/       # design-system primitives or thin wrappers
    ├── Button.tsx
    ├── Input.tsx
    └── ...
```

Boundary rules:

- Features import from components and primitives
- Components import from primitives
- Primitives don't import features or other domain code

Reverse imports break the dependency graph.

## Server state via TanStack Query

Server state (anything fetched from the API) lives in TanStack Query, not in `useState` or `useEffect`-with-fetch.

```tsx
// Good
function StudentDetail({ studentId }: { studentId: StudentId }) {
  const { data: student, isLoading, error } = useQuery({
    queryKey: ["student", studentId],
    queryFn: () => api.students.get(studentId),
  });

  if (isLoading) return <Skeleton />;
  if (error) return <ErrorState error={error} />;
  if (!student) return <NotFound />;

  return <StudentView student={student} />;
}

// Bad — manual fetch
function StudentDetail({ studentId }: { studentId: StudentId }) {
  const [student, setStudent] = useState<Student | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(`/api/students/${studentId}`)
      .then(r => r.json())
      .then(s => { setStudent(s); setLoading(false); });
  }, [studentId]);
  ...
}
```

Manual fetch loses caching, retry, refetch-on-mount, dedup, and a hundred other things TanStack Query handles. **Never manual-fetch in components.**

Forms and mutations have their own rules in `frontend-mutations.md`.

## Query key conventions

Hierarchical, predictable:

```ts
// Good
queryKey: ["student", studentId]
queryKey: ["students", { districtId, schoolId }]
queryKey: ["iep", iepId]
queryKey: ["iep-goals", iepId]
queryKey: ["compliance-deadlines", { districtId }]

// Bad
queryKey: ["alice"]
queryKey: [studentId]  // no namespace
```

The first element is the namespace. Subsequent elements are scope. Filters go in an object.

This pattern allows `queryClient.invalidateQueries({ queryKey: ["iep", iepId] })` to invalidate all queries scoped to one IEP.

## Dialogs

**Never render Chakra's `Dialog.Root` (or `Drawer.Root`, `Popover.Root`) directly.** Every dialog goes through Chakra's Overlay Manager via one of two factories:

- `createManagedDialog<T, R>(config)` from `web/src/components/ManagedDialog.tsx` — generic dialog shell with a typed `value` and a typed confirm `result`.
- `createReasonDialog<T>()` from `web/src/components/ReasonDialog.tsx` — for audit-ceremony actions (Retry, Revert, Reject, Release, Authorize, Revoke, Rotate, Adjust). Returns a textarea-plus-confirm dialog whose result is `{ reason, forced }`.

Both factories return `{ open, close, Viewport }`. The `Viewport` is rendered **once, at the app root** (in `App.tsx`, sibling to the page chrome and any Drawers). The `open(value)` call returns a Promise that resolves with the confirm result on confirm, or `undefined` on cancel.

```tsx
// Good — module-level dialog instance, rendered into a root Viewport.
const syncDetailDialog = createManagedDialog<SyncJobSummary>({
  size: "lg",
  title: (sync) => `Sync ${sync.id}`,
  body: (sync) => <SyncDetailBody sync={sync} />,
});

// In App.tsx (the only place dialogs render):
<syncDetailDialog.Viewport />

// In any handler, anywhere in the tree (including inside a Drawer):
const onClick = () => syncDetailDialog.open(sync);

// Good — reason-input dialog with typed action context.
const actionReasonDialog = createReasonDialog<{ kind: "retry"; sync: SyncJobSummary }>();

const onRetry = async (sync) => {
  const result = await actionReasonDialog.open({
    kind: "retry",
    sync,
    config: { title: `Retry sync ${sync.id}`, confirmLabel: "Retry" },
  });
  if (!result) return; // cancelled
  retryMutation.mutate({ syncId: sync.id, ...result });
};

// Bad — raw Dialog.Root rendered inside a Drawer subtree. Freezes the
// page because the focus trap / scroll lock / dismissable-layer stack
// gets corrupted by nesting.
<Drawer.Root open>
  <Drawer.Content>
    <Dialog.Root open={localOpen}> ... </Dialog.Root>
  </Drawer.Content>
</Drawer.Root>
```

**Why.** Chakra v3 (via Ark UI / Zag.js) tracks every open dialog/drawer/popover in a shared dismissable-layer stack. A Dialog rendered inside a Drawer's React subtree corrupts that stack: the focus trap, body scroll lock, and pointer-events bookkeeping survive after the inner Dialog closes, freezing every click on the page. The Overlay Manager solves this by registering each overlay against the same root-level stack regardless of where `.open()` is called from. This is the canonical Chakra v3 pattern; the docs at https://chakra-ui.com/docs/components/overlay-manager call it out explicitly for "dialogs opened from menu items inside other overlays." Bug history: ConnectorsPage Session 11 (first hit), revert/details Session 14 (caught by the `useLatchedValue` patch which wasn't enough), nested-dialog Session 14 (final structural fix to `createOverlay`).

**Rule.** A new dialog means: define a `create*Dialog` instance at module level, export it (so callers can `.open()` from anywhere), add its `Viewport` to the appropriate root outlet (`LeaDetailDialogOutlets`, `ConnectorDialogOutlets`, etc.) which `App.tsx` renders. Never render `Dialog.Root` / `Drawer.Root` / `Popover.Root` inline; never call `.open()` outside of an event handler or effect; never render a Viewport inside another modal.

## Client state via `useState` and context

UI state that doesn't come from the server (open dialogs, filter selections, expanded rows) lives in `useState`. Cross-component client state lives in React Context, not in a global store.

```tsx
// Good — local UI state
function StudentTable() {
  const [selectedRowId, setSelectedRowId] = useState<StudentId | null>(null);
  ...
}

// Good — cross-component UI state
const DistrictContext = createContext<DistrictContextValue | null>(null);

function DistrictProvider({ children }: { children: ReactNode }) {
  const [districtId, setLeaId] = useState<LeaId>(...);
  return <DistrictContext.Provider value={{ districtId, setLeaId }}>{children}</DistrictContext.Provider>;
}
```

Don't reach for Redux/Zustand for routine UI state. Most apps don't need a global store. If you find yourself wanting one, ask: is this server state (use TanStack Query) or local UI state (use useState/context)? It's almost always one of those.

## Hooks rules

Standard hooks discipline:

- Hooks at the top of the component, no early returns before all hooks have run
- Hook names start with `use`
- Custom hooks for non-trivial stateful logic
- No conditional hook calls

Custom hooks per feature:

```ts
// features/students/useStudentList.ts
export function useStudentList(filters: StudentFilters) {
  return useQuery({
    queryKey: ["students", filters],
    queryFn: () => api.students.list(filters),
    staleTime: 30_000,
  });
}
```

Components consume the hook; the hook encapsulates the query shape.

## Loading and error states

Every server-data component renders three states explicitly:

1. Loading
2. Error
3. Success (data present)

Plus optional empty state when success-but-no-data is meaningful.

```tsx
function StudentList() {
  const { data, isLoading, error } = useStudentList();

  if (isLoading) return <DataTableSkeleton />;
  if (error) return <ErrorBoundary error={error} retry={...} />;
  if (!data?.length) return <EmptyState message="No students match the current filter" />;

  return <DataTable rows={data} />;
}
```

Don't hide errors with `?.` chains that produce blank UI. Loading and error states are first-class deliverables.

## Conditional rendering

```tsx
// Good
{isVisible && <Component />}
{condition ? <A /> : <B />}

// Bad — numeric falsy renders 0
{items.length && <List items={items} />}  // renders 0 when empty!

// Fix
{items.length > 0 && <List items={items} />}
```

The `array.length && X` pattern is a classic JSX bug. Always compare explicitly.

## Lists need stable keys

```tsx
// Good
{students.map(student => <StudentRow key={student.id} student={student} />)}

// Bad — array index as key
{students.map((student, i) => <StudentRow key={i} student={student} />)}
```

Array indexes as keys cause React to misidentify rows when the list reorders. Always use a stable entity ID.

## Accessibility

Every interactive element has accessible labels:

```tsx
<button aria-label="Edit student" onClick={...}>
  <EditIcon />
</button>

<input
  aria-label="Search students"
  type="search"
  ...
/>
```

Keyboard navigation works: tab order is logical, focus is visible, Escape closes modals, Enter submits forms.

Color isn't the only differentiator: alerts have icons and labels, not just red color.

WCAG 2.1 AA is the baseline. Run `axe-core` in CI for automated checks.

## No business logic in components

Components compose primitives, fetch data via hooks, and render. Business logic lives in:

- The backend API (most logic)
- Custom hooks (presentation logic)
- Utility functions (pure computations)

```tsx
// Bad
function IEPSummary({ iep }: { iep: IEP }) {
  // Business logic in the component
  const daysToReview = differenceInDays(iep.annualReviewDate, new Date());
  const isOverdue = daysToReview < 0;
  const severity = daysToReview < 7 ? "warning" : daysToReview < 30 ? "info" : "ok";
  ...
}

// Good
function IEPSummary({ iep }: { iep: IEP }) {
  const reviewStatus = useIEPReviewStatus(iep);
  return <ReviewStatusBadge status={reviewStatus} />;
}

// In useIEPReviewStatus hook or utility
export function computeIEPReviewStatus(iep: IEP): IEPReviewStatus {
  ...
}
```

Better still: the backend computes the status and ships it with the IEP. Frontend just renders.

## Compliance display: don't drift from backend

When the UI displays compliance-related deadlines or statuses (per `.claude/rules/compliance.md`), the backend is authoritative. Don't recompute deadlines in the frontend. Display what the API ships:

```tsx
// Good — show backend-computed status
function DeadlineCell({ deadline }: { deadline: ComplianceDeadline }) {
  return (
    <Badge severity={deadline.severity}>
      {deadline.label} {deadline.daysRemaining > 0 ? `in ${deadline.daysRemaining} days` : "overdue"}
    </Badge>
  );
}

// Bad — frontend computes
function DeadlineCell({ iep }: { iep: IEP }) {
  const days = differenceInDays(iep.annualReviewDate, new Date());
  const severity = days < 7 ? "warning" : "ok";
  ...
}
```

Two sources of compliance truth is one too many. The backend is the regulatory surface; the frontend just shows the result.

## Testing

Component tests cover behavior, not snapshots. Use React Testing Library:

```tsx
test("StudentList shows empty state when no students match filter", async () => {
  render(<StudentList />, { wrapper: TestWrapper });
  await waitFor(() => {
    expect(screen.getByText(/no students match/i)).toBeInTheDocument();
  });
});
```

Mock TanStack Query at the boundary (mock `api.students.list` or use `msw` for HTTP). Don't mock React internals.

E2E tests use Playwright; they cover full flows (load page → filter → click row → see detail).

## Performance

For 95% of components, React's default rendering is fast enough. Don't reach for `memo`, `useMemo`, `useCallback` casually.

Reach for them when:

- A list of 100+ items re-renders on parent state change and is slow
- An expensive computation re-runs on each render and is measurable
- A child component re-renders unnecessarily because of unstable callback references

Profile first, optimize second.

## Internationalization

If the application supports multiple languages (likely needed for some districts), strings live in a translation catalog, not hardcoded JSX text. [future consideration]

```tsx
// Bad
<Button>Save changes</Button>

// Good
<Button>{t("common.save")}</Button>
```

## Cross-references

- `.claude/rules/frontend-forms.md` — form-specific patterns
- `.claude/rules/frontend-mutations.md` — TanStack Query mutations and optimistic updates
- `.claude/rules/copy-style.md` — UI copy
- `docs/UI_STANDARDS.md` — design system specifics
