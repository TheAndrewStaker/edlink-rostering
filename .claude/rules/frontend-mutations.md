---
paths:
  - web/src/features/**/use*Mutation*.{ts,tsx}
  - web/src/features/**/use*Create*.{ts,tsx}
  - web/src/features/**/use*Update*.{ts,tsx}
  - web/src/features/**/use*Delete*.{ts,tsx}
  - web/src/api/**/*.{ts,tsx}
---

# Frontend mutations

TanStack Query mutation patterns. Covers create/update/delete flows, optimistic updates, error handling, and cache invalidation.

## Use TanStack Query mutations

Every write operation that hits the API goes through `useMutation`. Manual `fetch().then()` is not allowed.

```tsx
function useCreateIEPGoal(iepId: IEPId) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (values: IEPGoalFormValues) => api.iepGoals.create(iepId, values),
    onSuccess: (newGoal) => {
      // Invalidate the goals list so it refetches
      queryClient.invalidateQueries({ queryKey: ["iep-goals", iepId] });
      // Optionally set the new goal in the cache so detail pages don't need to refetch
      queryClient.setQueryData(["iep-goal", newGoal.id], newGoal);
    },
    onError: (error, variables, context) => {
      // Logging is centralized in the mutation hook
      console.error("Failed to create IEP goal", error);
    },
  });
}
```

## Custom mutation hooks per operation

One hook per business operation: `useCreateIEPGoal`, `useUpdateIEPGoal`, `useDeleteIEPGoal`. Components use the hook; the hook owns the mutation contract.

```tsx
// In the form component
function IEPGoalForm({ iepId, onCreated }: IEPGoalFormProps) {
  const createMutation = useCreateIEPGoal(iepId);

  const onSubmit = async (values: IEPGoalFormValues) => {
    const result = await createMutation.mutateAsync(values);
    onCreated(result);
  };
  ...
}
```

This pattern centralizes cache invalidation logic per operation. Don't scatter `invalidateQueries` calls across components.

## Idempotency on writes

Every mutation request includes an `Idempotency-Key` header (per `.claude/rules/integration-protocol.md`):

```tsx
const api = {
  iepGoals: {
    create: (iepId: IEPId, values: IEPGoalFormValues) =>
      httpClient.post(`/ieps/${iepId}/goals`, values, {
        headers: { "Idempotency-Key": crypto.randomUUID() },
      }),
  },
};
```

The idempotency key is generated client-side and survives retries (TanStack Query's built-in retry uses the same key).

## Cache invalidation strategy

After a successful mutation, invalidate or update the relevant query caches.

**Invalidate** (triggers refetch on next query subscription):

```tsx
queryClient.invalidateQueries({ queryKey: ["iep-goals", iepId] });
```

**Set directly** (no refetch needed, use the mutation result):

```tsx
queryClient.setQueryData(["iep-goal", newGoal.id], newGoal);
```

Default to invalidation unless you have high confidence the mutation response matches what the list endpoint would return (often it doesn't — list endpoints include computed fields, summaries, etc.).

## Optimistic updates

For mutations on existing data where the result is visible immediately, use optimistic updates:

```tsx
function useUpdateIEPGoal(goalId: IEPGoalId, iepId: IEPId) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (values: IEPGoalFormValues) => api.iepGoals.update(goalId, values),

    onMutate: async (newValues) => {
      // Cancel in-flight queries that would overwrite our optimistic update
      await queryClient.cancelQueries({ queryKey: ["iep-goal", goalId] });
      await queryClient.cancelQueries({ queryKey: ["iep-goals", iepId] });

      // Snapshot previous state
      const previousGoal = queryClient.getQueryData<IEPGoal>(["iep-goal", goalId]);
      const previousGoals = queryClient.getQueryData<IEPGoal[]>(["iep-goals", iepId]);

      // Optimistically update
      if (previousGoal) {
        queryClient.setQueryData<IEPGoal>(["iep-goal", goalId], { ...previousGoal, ...newValues });
      }
      if (previousGoals) {
        queryClient.setQueryData<IEPGoal[]>(["iep-goals", iepId], previousGoals.map(g =>
          g.id === goalId ? { ...g, ...newValues } : g
        ));
      }

      // Return rollback context
      return { previousGoal, previousGoals };
    },

    onError: (error, variables, context) => {
      // Roll back on failure
      if (context?.previousGoal) {
        queryClient.setQueryData(["iep-goal", goalId], context.previousGoal);
      }
      if (context?.previousGoals) {
        queryClient.setQueryData(["iep-goals", iepId], context.previousGoals);
      }
    },

    onSettled: () => {
      // Always refetch to ensure we have authoritative server state
      queryClient.invalidateQueries({ queryKey: ["iep-goal", goalId] });
      queryClient.invalidateQueries({ queryKey: ["iep-goals", iepId] });
    },
  });
}
```

**Optimistic updates are not appropriate for every mutation.** Use them when:

- The change is purely additive or simple (toggle a flag, edit a name)
- Server validation rarely rejects (the operation is "near-certain")
- Visible immediacy improves UX significantly

Don't use them for:

- Mutations with server-side cascading effects
- Mutations that might trigger compliance failures (IEP amendments)
- Mutations where rollback would be confusing

## Pessimistic updates (default)

For most mutations, pessimistic (wait for server response, then invalidate) is correct:

```tsx
function useAmendIEP(iepId: IEPId) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (changes: IEPAmendment) => api.ieps.amend(iepId, changes),
    onSuccess: (newSnapshot) => {
      queryClient.invalidateQueries({ queryKey: ["iep", iepId] });
      queryClient.invalidateQueries({ queryKey: ["iep-history", iepId] });
      queryClient.invalidateQueries({ queryKey: ["compliance-deadlines"] });
    },
  });
}
```

IEP amendments are legal events. Optimism is wrong here — wait for confirmation.

## Loading states for mutations

Components show mutation progress:

```tsx
function IEPGoalForm({ iepId, onCreated }: IEPGoalFormProps) {
  const createMutation = useCreateIEPGoal(iepId);

  return (
    <form onSubmit={form.handleSubmit((v) => createMutation.mutateAsync(v).then(onCreated))}>
      ...
      <Button type="submit" disabled={createMutation.isPending}>
        {createMutation.isPending ? "Saving..." : "Save"}
      </Button>
      {createMutation.isError && (
        <Alert severity="error">{createMutation.error.message}</Alert>
      )}
    </form>
  );
}
```

`isPending` is the new TanStack Query 5 name (was `isLoading` in v4).

## Retry behavior

Default TanStack Query retry behavior is fine for most cases (3 retries with exponential backoff). Override for mutations that shouldn't retry:

```tsx
useMutation({
  mutationFn: ...,
  retry: false,  // for non-idempotent operations without idempotency key support
});
```

For LTI grade passback and similar partner write operations, retry is essential and idempotency keys make it safe.

## Bulk mutations

For operations on many entities at once (e.g., bulk roster sync trigger):

```tsx
function useSyncRoster(districtId: LeaId) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.roster.sync(districtId),
    onSuccess: () => {
      // Bulk sync invalidates a lot
      queryClient.invalidateQueries({ queryKey: ["students"] });
      queryClient.invalidateQueries({ queryKey: ["classes"] });
      queryClient.invalidateQueries({ queryKey: ["enrollments"] });
      queryClient.invalidateQueries({ queryKey: ["teachers"] });
    },
  });
}
```

For partial bulk failures, mutations return per-item results; the component shows a summary.

## Mutation error mapping

Backend errors come in standard shape (per backend API conventions):

```json
{
  "error": "validation_failed",
  "message": "Some fields are invalid",
  "fields": {
    "goalText": "Goal must be at least 20 characters"
  }
}
```

Frontend converts to typed errors:

```tsx
class FieldValidationError extends Error {
  constructor(public field: string, message: string) { super(message); }
}

async function handleResponse<T>(response: Response): Promise<T> {
  if (response.ok) return response.json();
  const body = await response.json();
  if (body.error === "validation_failed" && body.fields) {
    const [field, message] = Object.entries(body.fields)[0] as [string, string];
    throw new FieldValidationError(field, message);
  }
  throw new ApiError(body.message ?? "Request failed", response.status);
}
```

Forms catch `FieldValidationError` and route to the right field. Other errors surface as form-level errors.

## Server actions / Next.js Route Handlers

If the application uses Next.js with Server Actions: server actions co-exist with TanStack Query. Use server actions for form posts that benefit from progressive enhancement; use TanStack Query mutations for everything else.

For Vite-based React: ignore this section.

## Audit log on the backend

Per `.claude/rules/security.md`, every mutation produces an audit log entry on the backend. The frontend doesn't need to handle audit logging directly — it happens automatically server-side.

## Cross-references

- `.claude/rules/frontend-forms.md` — form patterns
- `.claude/rules/frontend-components.md` — broader component patterns
- `.claude/rules/integration-protocol.md` — idempotency keys
- `.claude/rules/security.md` — audit logging
