---
paths:
  - web/src/features/**/*Form*.tsx
  - web/src/features/**/*Edit*.tsx
  - web/src/features/**/*Create*.tsx
  - web/src/components/forms/**/*.tsx
---

# Frontend forms

Form patterns for the React UI. Cover validation, error display, dirty-state tracking, and the edit-vs-create flow.

## Library

Current approach: **`react-hook-form` + `zod` for schema validation.** Combines type-safety, performance (uncontrolled inputs by default), and ergonomic validation.

```tsx
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";

const iepGoalSchema = z.object({
  goalText: z.string().min(20, "Goal must be at least 20 characters").max(2000),
  goalDomain: z.enum(["ReadingFluency", "Mathematics", "Behavior", "SpeechLanguage"]),
  measurementMethod: z.string().min(1, "Measurement method is required"),
  baseline: z.string().min(1, "Baseline is required"),
  targetMeasure: z.string().min(1, "Target measure is required"),
});

type IEPGoalFormValues = z.infer<typeof iepGoalSchema>;

function IEPGoalForm({ initialValues, onSubmit }: IEPGoalFormProps) {
  const form = useForm<IEPGoalFormValues>({
    resolver: zodResolver(iepGoalSchema),
    defaultValues: initialValues,
  });

  return (
    <form onSubmit={form.handleSubmit(onSubmit)}>
      <TextField
        {...form.register("goalText")}
        label="Annual goal"
        error={!!form.formState.errors.goalText}
        helperText={form.formState.errors.goalText?.message}
        multiline
      />
      ...
      <Button type="submit" disabled={!form.formState.isDirty || form.formState.isSubmitting}>
        Save
      </Button>
    </form>
  );
}
```

## Schema-first validation

Define a Zod schema for every form. The schema is:

1. The source of truth for validation rules
2. Reused for the type definition via `z.infer`
3. Mirrored on the backend (Pydantic) so client and server agree

When backend validation rules change, update the Zod schema in the same PR.

## Validation taxonomy

Three categories of validation:

1. **Format validation** — type, length, regex (Zod handles)
2. **Cross-field validation** — "end date must be after start date" (Zod's `.refine`)
3. **Server-side validation** — "this email is already used by another teacher" (backend-only, surfaced via error)

Client validates 1 and 2 for fast feedback. Server validates all three. **Never rely solely on client validation.**

## Dirty state matters

Forms track whether the user has changed anything. Buttons reflect this:

```tsx
const { formState: { isDirty, isSubmitting, isValid } } = form;

<Button
  type="submit"
  disabled={!isDirty || isSubmitting || !isValid}
>
  {isSubmitting ? "Saving..." : "Save changes"}
</Button>
```

Don't let users submit unchanged forms (the API call is wasted).

Don't let users navigate away from a dirty form without confirmation:

```tsx
useBeforeUnload(isDirty, "You have unsaved changes. Leave anyway?");
```

## Edit vs create flow

Two distinct shapes, same form component:

```tsx
type IEPGoalFormProps =
  | { mode: "create"; iepId: IEPId; onCreated: (goal: IEPGoal) => void }
  | { mode: "edit"; goal: IEPGoal; onUpdated: (goal: IEPGoal) => void };

function IEPGoalForm(props: IEPGoalFormProps) {
  const initialValues = props.mode === "edit" ? props.goal : emptyGoal();
  const form = useForm<IEPGoalFormValues>({ resolver: zodResolver(iepGoalSchema), defaultValues: initialValues });

  const mutation = useIEPGoalMutation(props.mode === "edit" ? "update" : "create");

  return (
    <form onSubmit={form.handleSubmit(async (values) => {
      const result = await mutation.mutateAsync(values);
      if (props.mode === "edit") props.onUpdated(result);
      else props.onCreated(result);
    })}>
      ...
    </form>
  );
}
```

The form structure is the same; the mode controls the mutation and the callback. See `.claude/rules/frontend-mutations.md` for the mutation hook pattern.

## Two-slot edit pattern

For "edit existing entity" flows, separate the data fetch from the form:

```tsx
function EditIEPGoalPage({ goalId }: { goalId: IEPGoalId }) {
  const { data: goal, isLoading, error } = useQuery({
    queryKey: ["iep-goal", goalId],
    queryFn: () => api.iepGoals.get(goalId),
  });

  if (isLoading) return <FormSkeleton />;
  if (error) return <ErrorState error={error} />;
  if (!goal) return <NotFound />;

  return <IEPGoalForm mode="edit" goal={goal} onUpdated={...} />;
}
```

The page fetches; the form receives a complete entity. The form doesn't fetch (separation of concerns).

## Error display

Field errors near the field. Form-level errors at the top. Server errors mapped to the right field when possible.

```tsx
const [formError, setFormError] = useState<string | null>(null);

const onSubmit = async (values: IEPGoalFormValues) => {
  setFormError(null);
  try {
    const result = await mutation.mutateAsync(values);
    onSuccess(result);
  } catch (e) {
    if (e instanceof FieldValidationError) {
      // Server told us which field is wrong
      form.setError(e.field, { message: e.message });
    } else if (e instanceof Error) {
      setFormError(e.message);
    } else {
      setFormError("Something went wrong. Please try again.");
    }
  }
};

return (
  <form onSubmit={form.handleSubmit(onSubmit)}>
    {formError && <Alert severity="error">{formError}</Alert>}
    <TextField {...form.register("goalText")} error={!!form.formState.errors.goalText} ... />
    ...
  </form>
);
```

## Optimistic updates and rollback

For forms that update existing data, optimistic updates feel snappy but require rollback handling. See `.claude/rules/frontend-mutations.md` for the pattern.

## Field types and constraints

| Field type | Constraints to enforce |
|---|---|
| Email | Format validation; case normalization on submit |
| Phone | Format validation; storage format consistency |
| Date | Format; range validation; timezone handling |
| Multi-select | Min/max count |
| File upload | Type, size, virus scan |
| Free text (long) | Max length; markdown sanitization if rendered |

## Specifically for IEP forms

IEP forms have legal implications:

1. **Validate completeness** against IDEA's required elements (per `.claude/rules/compliance.md` and IDEA 34 CFR § 300.320). Missing transition plan for age-16+ student: visible warning, not silent omission.
2. **Service minutes are numbers** — int validation, positive only, sane upper bound
3. **Goal text is freeform but length-bounded** — too short suggests incompleteness; too long suggests unstructured data
4. **Provider references are dropdown-from-roster** — don't free-text-enter staff names; pick from rostered staff
5. **Effective dates** — validate against academic calendar (no IEP effective during summer in most cases)

These validations live in the Zod schema AND the backend Pydantic schema. Keep them in sync.

## Accessibility

- Every input has an associated `<label>`
- Errors are associated via `aria-describedby`
- Required fields have `aria-required="true"`
- Submit button is keyboard-accessible
- Focus moves to the first error on submit failure

## File uploads

Per `.claude/rules/security.md`: validated server-side, never trust client size/type checks alone. Client-side check is for UX (warn before upload starts); server check is the security boundary.

```tsx
<input
  type="file"
  accept="application/pdf,image/png,image/jpeg"
  onChange={(e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (file.size > MAX_UPLOAD_BYTES) {
      setFormError("File too large (max 10 MB)");
      return;
    }
    // ... continue with upload
  }}
/>
```

## Submit-once protection

Disable submit button while mutation is in-flight to prevent double-submit:

```tsx
<Button type="submit" disabled={form.formState.isSubmitting || mutation.isPending}>
```

Backend should also enforce idempotency via `Idempotency-Key` header per `.claude/rules/integration-protocol.md`.

## Cross-references

- `.claude/rules/frontend-components.md` — broader React patterns
- `.claude/rules/frontend-mutations.md` — TanStack Query mutations
- `.claude/rules/copy-style.md` — UI copy
- `.claude/rules/compliance.md` — backend validation rules form mirrors
