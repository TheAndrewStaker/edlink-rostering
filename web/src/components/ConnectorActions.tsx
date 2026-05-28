/**
 * Phase 1.5d connector lifecycle action dialogs.
 *
 * Four small dialogs share the same shape: header + body fields +
 * Confirm/Cancel footer. Each is a module-level instance built with
 * `createManagedDialog` (or `createReasonDialog` for the
 * reason-only Revoke) so the actual Dialog.Root renders into a
 * Viewport at the app root, not inside the page or Drawer that
 * triggers them. See `ManagedDialog.tsx` for the rationale.
 *
 * Public API per dialog:
 *  - `<Dialog>.open(value)` returns a Promise that resolves with the
 *    operator's submitted input on confirm, or `undefined` on
 *    cancel.
 *  - `<Dialog>.Viewport` is rendered once at the app root (via
 *    `ConnectorDialogOutlets`).
 *
 * `ConnectorsPage.tsx` is the only call site; it owns the mutations
 * and the toaster and dispatches based on the resolved result.
 */

import {
  Button,
  Field,
  HStack,
  Input,
  Slider,
  Stack,
  Text,
  Textarea,
} from "@chakra-ui/react";
import { useEffect, useState } from "react";

import type { ConnectorAuthorizationOut } from "@/api/client";
import {
  createManagedDialog,
  type ManagedDialogContext,
} from "@/components/ManagedDialog";
import { createReasonDialog } from "@/components/ReasonDialog";
import { formatPollInterval, labelForPartner } from "@/lib/labels";

function subtitleFor(row: ConnectorAuthorizationOut): string {
  return `${row.lea_name} (${labelForPartner(row.partner)})`;
}

// ── Authorize ──────────────────────────────────────────────────────────────

export interface AuthorizeInput {
  secret_ref: string;
  reason: string;
  poll_interval_seconds: number;
}

export const authorizeConnectorDialog = createManagedDialog<
  { row: ConnectorAuthorizationOut },
  AuthorizeInput
>({
  size: "md",
  title: () => "Authorize connector",
  subtitle: (v) => subtitleFor(v.row),
  body: (v, ctx) => <AuthorizeBody row={v.row} ctx={ctx} />,
});

function AuthorizeBody({
  row,
  ctx,
}: {
  row: ConnectorAuthorizationOut;
  ctx: ManagedDialogContext<AuthorizeInput>;
}) {
  const [secretRef, setSecretRef] = useState(row.secret_ref);
  const [reason, setReason] = useState("");
  const [pollInterval, setPollInterval] = useState<number>(
    row.poll_interval_seconds,
  );

  useEffect(() => {
    setSecretRef(row.secret_ref);
    setReason("");
    setPollInterval(row.poll_interval_seconds);
  }, [row.id, row.secret_ref, row.poll_interval_seconds]);

  const valid = secretRef.trim().length > 0 && reason.trim().length > 0;

  return (
    <Stack gap={4}>
      <Field.Root required>
        <Field.Label>Key Vault secret name</Field.Label>
        <Input
          autoFocus
          placeholder="edlink-token-lea-..."
          value={secretRef}
          onChange={(e) => setSecretRef(e.target.value)}
        />
        <Field.HelperText>
          The bearer token must be staged in the vault before authorize;
          the server verifies presence before flipping the row to
          active.
        </Field.HelperText>
      </Field.Root>
      <Field.Root>
        <Field.Label>
          Poll interval: {formatPollInterval(pollInterval)}
        </Field.Label>
        <PollIntervalSlider value={pollInterval} onChange={setPollInterval} />
        <Field.HelperText>
          1 minute (tightest) to 1 hour (loosest). Default is 5 minutes.
        </Field.HelperText>
      </Field.Root>
      <Field.Root required>
        <Field.Label>Reason</Field.Label>
        <Textarea
          rows={3}
          placeholder="e.g. initial onboarding for pilot LEA"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
        <Field.HelperText>
          Recorded on the audit row with your operator identity.
        </Field.HelperText>
      </Field.Root>
      <DialogActions
        onClose={ctx.close}
        confirmLabel="Authorize"
        confirmPalette="blue"
        disabled={!valid}
        onConfirm={() =>
          ctx.confirm({
            secret_ref: secretRef.trim(),
            reason: reason.trim(),
            poll_interval_seconds: pollInterval,
          })
        }
      />
    </Stack>
  );
}

// ── Revoke ─────────────────────────────────────────────────────────────────

export const revokeConnectorDialog = createReasonDialog<{
  row: ConnectorAuthorizationOut;
}>();

// ── Rotate credential ──────────────────────────────────────────────────────

export interface RotateInput {
  new_secret_ref: string;
  reason: string;
}

export const rotateCredentialDialog = createManagedDialog<
  { row: ConnectorAuthorizationOut },
  RotateInput
>({
  size: "md",
  title: () => "Rotate credential",
  subtitle: (v) => subtitleFor(v.row),
  body: (v, ctx) => <RotateBody row={v.row} ctx={ctx} />,
});

function RotateBody({
  row,
  ctx,
}: {
  row: ConnectorAuthorizationOut;
  ctx: ManagedDialogContext<RotateInput>;
}) {
  const [newSecretRef, setNewSecretRef] = useState("");
  const [reason, setReason] = useState("");

  useEffect(() => {
    setNewSecretRef("");
    setReason("");
  }, [row.id]);

  const valid = newSecretRef.trim().length > 0 && reason.trim().length > 0;

  return (
    <Stack gap={4}>
      <Text fontSize="sm" color="gray.700">
        Stage the new token in Key Vault first; this dialog verifies
        the new name resolves before swapping.
      </Text>
      <Field.Root>
        <Field.Label>Current secret</Field.Label>
        <Text fontFamily="mono" fontSize="sm" color="gray.600">
          {row.secret_ref}
        </Text>
      </Field.Root>
      <Field.Root required>
        <Field.Label>New Key Vault secret name</Field.Label>
        <Input
          autoFocus
          placeholder="edlink-token-lea-...-v2"
          value={newSecretRef}
          onChange={(e) => setNewSecretRef(e.target.value)}
        />
      </Field.Root>
      <Field.Root required>
        <Field.Label>Reason</Field.Label>
        <Textarea
          rows={3}
          placeholder="e.g. annual rotation"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
      </Field.Root>
      <DialogActions
        onClose={ctx.close}
        confirmLabel="Rotate"
        confirmPalette="purple"
        disabled={!valid}
        onConfirm={() =>
          ctx.confirm({
            new_secret_ref: newSecretRef.trim(),
            reason: reason.trim(),
          })
        }
      />
    </Stack>
  );
}

// ── Adjust poll interval ───────────────────────────────────────────────────

export interface AdjustInput {
  new_poll_interval_seconds: number;
  reason: string;
}

export const adjustPollIntervalDialog = createManagedDialog<
  { row: ConnectorAuthorizationOut },
  AdjustInput
>({
  size: "md",
  title: () => "Adjust poll interval",
  subtitle: (v) => subtitleFor(v.row),
  body: (v, ctx) => <AdjustBody row={v.row} ctx={ctx} />,
});

function AdjustBody({
  row,
  ctx,
}: {
  row: ConnectorAuthorizationOut;
  ctx: ManagedDialogContext<AdjustInput>;
}) {
  const [interval, setIntervalValue] = useState(row.poll_interval_seconds);
  const [reason, setReason] = useState("");

  useEffect(() => {
    setIntervalValue(row.poll_interval_seconds);
    setReason("");
  }, [row.id, row.poll_interval_seconds]);

  const valid =
    reason.trim().length > 0 && interval >= 60 && interval <= 3600;

  return (
    <Stack gap={4}>
      <Field.Root>
        <Field.Label>
          Poll interval: {formatPollInterval(interval)} (current{" "}
          {formatPollInterval(row.poll_interval_seconds)})
        </Field.Label>
        <PollIntervalSlider value={interval} onChange={setIntervalValue} />
        <Field.HelperText>
          Tighter polls give fresher data and use more EdLink rate-limit
          budget. The default 5 minutes suits most LEAs.
        </Field.HelperText>
      </Field.Root>
      <Field.Root required>
        <Field.Label>Reason</Field.Label>
        <Textarea
          autoFocus
          rows={3}
          placeholder="e.g. match LEA SIS refresh cadence"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
      </Field.Root>
      <DialogActions
        onClose={ctx.close}
        confirmLabel="Apply"
        confirmPalette="blue"
        disabled={!valid}
        onConfirm={() =>
          ctx.confirm({
            new_poll_interval_seconds: interval,
            reason: reason.trim(),
          })
        }
      />
    </Stack>
  );
}

// ── Outlets ────────────────────────────────────────────────────────────────

export function ConnectorDialogOutlets() {
  return (
    <>
      <authorizeConnectorDialog.Viewport />
      <revokeConnectorDialog.Viewport />
      <rotateCredentialDialog.Viewport />
      <adjustPollIntervalDialog.Viewport />
    </>
  );
}

// ── Shared bits ────────────────────────────────────────────────────────────

function PollIntervalSlider({
  value,
  onChange,
}: {
  value: number;
  onChange: (v: number) => void;
}) {
  return (
    <Slider.Root
      value={[value]}
      min={60}
      max={3600}
      step={60}
      onValueChange={(e) => onChange(e.value[0]!)}
      width="full"
    >
      <Slider.Control>
        <Slider.Track height="6px" rounded="full" bg="gray.200">
          <Slider.Range bg="blue.500" rounded="full" />
        </Slider.Track>
        <Slider.Thumb
          index={0}
          boxSize="16px"
          bg="white"
          borderWidth="2px"
          borderColor="blue.500"
          shadow="sm"
        >
          <Slider.HiddenInput />
        </Slider.Thumb>
      </Slider.Control>
    </Slider.Root>
  );
}

function DialogActions({
  onClose,
  onConfirm,
  confirmLabel,
  confirmPalette,
  disabled,
}: {
  onClose: () => void;
  onConfirm: () => void;
  confirmLabel: string;
  confirmPalette: "blue" | "red" | "purple" | "green" | "orange";
  disabled: boolean;
}) {
  return (
    <HStack justify="flex-end" gap={3} pt={2}>
      <Button variant="ghost" onClick={onClose}>
        Cancel
      </Button>
      <Button
        colorPalette={confirmPalette}
        disabled={disabled}
        onClick={onConfirm}
      >
        {confirmLabel}
      </Button>
    </HStack>
  );
}
