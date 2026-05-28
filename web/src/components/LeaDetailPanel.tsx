/**
 * LEA detail Drawer.
 *
 * Phase 1 admin surface: right-side Drawer that opens when the operator
 * selects an LEA row. The master table stays visible behind, so
 * incident response can compare LEAs without losing context.
 *
 * Three sections, each with action buttons:
 *
 * 1. Cursor state — partner cursor with a freshness badge.
 * 2. Sync timeline — recent sync_jobs with retry/revert actions per row.
 * 3. Quarantine queue — unresolved orphans with release/reject actions.
 *
 * Action ceremonies (retry, revert, reject) capture an audit reason via
 * the ReasonDialog component, never via window.prompt(). Retry on a
 * successful sync surfaces the --force flag as an explicit checkbox.
 */

import {
  Badge,
  Box,
  Button,
  CloseButton,
  Code,
  Drawer,
  EmptyState,
  Flex,
  HStack,
  Heading,
  Link as ChakraLink,
  Portal,
  Skeleton,
  Stack,
  Table,
  Text,
} from "@chakra-ui/react";
import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { Fragment } from "react";

import {
  api,
  type ConnectorAuthorizationOut,
  type CursorStateRow,
  type QuarantineRowOut,
  type ReconciliationRunRow,
  type SyncJobSummary,
  type TimelineEntryOut,
  type ValidationIssueRow,
} from "@/api/client";
import { createManagedDialog } from "@/components/ManagedDialog";
import {
  createReasonDialog,
  type ReasonDialogConfig,
} from "@/components/ReasonDialog";
import { SendTestEventMenu } from "@/components/SendTestEventMenu";
import {
  colorForTimelineSource,
  combinedStatusView,
  formatPollInterval,
  labelForEntityType,
  labelForErrorCode,
  labelForLeaType,
  labelForPartner,
  labelForReconciliationStatus,
  labelForSharingScope,
  labelForSyncStatus,
  labelForTimelineAction,
  labelForTimelineSource,
  summarizeErrorSummary,
} from "@/lib/labels";
import { notifyError, notifySuccess } from "@/lib/notify";

interface Props {
  leaId: string | null;
  onClose: () => void;
}

const STATUS_COLOR: Record<string, string> = {
  success: "green",
  failed: "red",
  running: "blue",
  revert: "purple",
  quarantine_release: "gray",
};

interface PendingAction {
  kind: "retry" | "revert";
  sync: SyncJobSummary;
}

/**
 * Reason-input dialog context. The action kind determines which
 * mutation fires when the operator confirms.
 */
type ReasonContext =
  | { kind: "retry"; sync: SyncJobSummary }
  | { kind: "revert"; sync: SyncJobSummary }
  | { kind: "reject"; row: QuarantineRowOut };

// Module-level dialog instances. Both are registered with Chakra's
// Overlay Manager so the actual Dialog.Root renders into a Viewport
// at the app root, not inside the Drawer subtree that triggers them.
// This is the canonical Chakra v3 pattern for opening a Dialog from
// inside a Drawer (see ManagedDialog.tsx for the longer rationale).
// Render `LeaDetailDialogOutlets` once at the app root.
const syncDetailDialog = createManagedDialog<SyncJobSummary>({
  size: "lg",
  title: (sync) => (
    <>
      Sync detail{" "}
      <Code fontSize="sm" ml={1}>
        {sync.id.slice(0, 8)}
      </Code>
    </>
  ),
  body: (sync) => <SyncDetailBody sync={sync} />,
});

const actionReasonDialog = createReasonDialog<ReasonContext>();

export function LeaDetailDialogOutlets() {
  return (
    <>
      <syncDetailDialog.Viewport />
      <actionReasonDialog.Viewport />
    </>
  );
}

export function LeaDetailPanel({ leaId, onClose }: Props) {
  return (
    <Drawer.Root
      open={leaId != null}
      placement="end"
      size="xl"
      onOpenChange={(e) => {
        if (!e.open) onClose();
      }}
    >
      <Portal>
        <Drawer.Backdrop />
        <Drawer.Positioner>
          <Drawer.Content>
            {leaId && <DrawerInner leaId={leaId} onClose={onClose} />}
          </Drawer.Content>
        </Drawer.Positioner>
      </Portal>
    </Drawer.Root>
  );
}

function DrawerInner({ leaId, onClose }: { leaId: string; onClose: () => void }) {
  const leas = useQuery({ queryKey: ["leas"], queryFn: api.listLeas });
  const lea = (leas.data ?? []).find((l) => l.id === leaId);
  const handlers = useLeaActionHandlers(leaId);

  return (
    <>
      <Drawer.Header borderBottomWidth="1px" borderColor="gray.200">
        <Flex align="center" justify="space-between" w="full">
          <Box>
            <Heading size="md">{lea?.name ?? leaId}</Heading>
            {lea && (
              <Text fontSize="xs" color="gray.500" mt={1}>
                {lea.state} · {labelForLeaType(lea.lea_type)}
                <Text as="span" color="gray.400" ml={2} fontFamily="mono">
                  {lea.id}
                </Text>
              </Text>
            )}
          </Box>
          <HStack gap={2}>
            <SendTestEventMenu leaId={leaId} />
            <CloseButton size="sm" onClick={onClose} />
          </HStack>
        </Flex>
      </Drawer.Header>
      <Drawer.Body>
        <Stack gap={6} py={4}>
          <CursorSection leaId={leaId} />
          <SyncsSection
            leaId={leaId}
            onAction={handlers.onAction}
            onDetails={handlers.onDetails}
          />
          <ReconciliationDriftSection leaId={leaId} />
          <QuarantineSection leaId={leaId} onReject={handlers.onReject} />
          <AdminTimelineSection leaId={leaId} />
          <IntegrationSection leaId={leaId} />
        </Stack>
      </Drawer.Body>
    </>
  );
}

interface LeaActionHandlers {
  onAction: (action: PendingAction) => void;
  onReject: (row: QuarantineRowOut) => void;
  onDetails: (sync: SyncJobSummary) => void;
}

/**
 * Mutations + click handlers for the LEA detail surface. Each
 * handler opens the matching dialog via the Overlay Manager and
 * awaits the result before dispatching the mutation.
 */
function useLeaActionHandlers(leaId: string): LeaActionHandlers {
  const qc = useQueryClient();

  // The action mutations close the dialog on send (optimistic from
  // the operator's perspective) and rely on onError + a toast to
  // surface a server-side refusal. invalidateLea on onSettled means
  // the server-side truth resolves the optimistic guess. Per the
  // global CLAUDE.md mutation rule.
  const retryMutation = useMutation({
    mutationFn: ({
      syncId,
      reason,
      forced,
    }: {
      syncId: string;
      reason: string;
      forced: boolean;
    }) => api.retrySync(syncId, reason, forced),
    onSuccess: () => {
      notifySuccess(
        "Retry queued",
        "Cursor rewound; the next poll replays from there.",
      );
    },
    onError: (err) => {
      notifyError(
        "Retry failed",
        err instanceof Error ? err.message : String(err),
      );
    },
    onSettled: () => {
      invalidateLea(qc, leaId);
    },
  });
  const revertMutation = useMutation({
    mutationFn: ({ syncId, reason }: { syncId: string; reason: string }) =>
      api.revertSync(syncId, reason),
    onSuccess: (result) => {
      notifySuccess(
        "Revert applied",
        `${result.snapshots_restored} snapshot${
          result.snapshots_restored === 1 ? "" : "s"
        } restored.`,
      );
    },
    onError: (err) => {
      notifyError(
        "Revert failed",
        err instanceof Error ? err.message : String(err),
      );
    },
    onSettled: () => {
      invalidateLea(qc, leaId);
    },
  });
  const rejectMutation = useMutation({
    mutationFn: ({
      quarantineId,
      reason,
    }: {
      quarantineId: string;
      reason: string;
    }) => api.rejectQuarantine(quarantineId, reason),
    onMutate: async ({ quarantineId }) => {
      // Optimistic remove: drop the row from the cached list so the
      // panel reflects the action before the round-trip finishes.
      // onError rolls back from the snapshot.
      await qc.cancelQueries({ queryKey: ["quarantine", leaId] });
      const previous = qc.getQueryData<QuarantineRowOut[]>([
        "quarantine",
        leaId,
      ]);
      qc.setQueryData<QuarantineRowOut[]>(
        ["quarantine", leaId],
        (rows) => (rows ?? []).filter((r) => r.id !== quarantineId),
      );
      return { previous };
    },
    onSuccess: () => {
      notifySuccess(
        "Quarantine rejected",
        "Row marked resolved without canonical change.",
      );
    },
    onError: (err, _vars, ctx) => {
      if (ctx?.previous !== undefined) {
        qc.setQueryData(["quarantine", leaId], ctx.previous);
      }
      notifyError(
        "Reject failed",
        err instanceof Error ? err.message : String(err),
      );
    },
    onSettled: () => {
      invalidateLea(qc, leaId);
    },
  });

  const onAction = async (action: PendingAction) => {
    const config: ReasonDialogConfig =
      action.kind === "retry"
        ? {
            title: `Retry sync ${action.sync.id.slice(0, 8)}`,
            description: `Rewinds cursor to ${action.sync.cursor_before ?? "(empty)"} and writes a retry_actions audit row. The next poll replays from the rewound cursor.`,
            defaultReason: "operator-initiated retry",
            confirmLabel: "Retry",
            confirmPalette: "blue",
            showForced: action.sync.status === "success",
            forcedLabel: "Force retry on a successful sync",
            forcedDefault: false,
          }
        : {
            title: `Revert sync ${action.sync.id.slice(0, 8)}`,
            description:
              "Marks the sync's snapshots as reverted, restores the prior snapshots, and writes a revert_actions audit row. Soft-delete only.",
            defaultReason: "operator-initiated revert",
            confirmLabel: "Revert",
            confirmPalette: "purple",
          };
    const result = await actionReasonDialog.open({ ...action, config });
    if (!result) return;
    if (action.kind === "retry") {
      retryMutation.mutate({
        syncId: action.sync.id,
        reason: result.reason,
        forced: result.forced,
      });
    } else {
      revertMutation.mutate({
        syncId: action.sync.id,
        reason: result.reason,
      });
    }
  };

  const onReject = async (row: QuarantineRowOut) => {
    const result = await actionReasonDialog.open({
      kind: "reject",
      row,
      config: {
        title: `Reject ${row.entity_type}/${row.entity_id}`,
        description:
          "Marks the quarantine row resolved without applying it to canonical. Records the rejection reason for audit.",
        defaultReason: "",
        confirmLabel: "Reject",
        confirmPalette: "red",
      },
    });
    if (!result) return;
    rejectMutation.mutate({
      quarantineId: row.id,
      reason: result.reason,
    });
  };

  const onDetails = (sync: SyncJobSummary) => {
    void syncDetailDialog.open(sync);
  };

  return { onAction, onReject, onDetails };
}

// ── Cursor ───────────────────────────────────────────────────────────────────

function CursorSection({ leaId }: { leaId: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ["cursors", leaId],
    queryFn: () => api.listCursors(leaId),
  });
  return (
    <Box>
      <Heading size="sm" mb={2}>
        Cursor state
      </Heading>
      {isLoading && <Skeleton height="48px" />}
      {!isLoading && (!data || data.length === 0) && (
        <Text fontSize="sm" color="gray.500">
          No cursor recorded for this LEA yet.
        </Text>
      )}
      {data && data.length > 0 && (
        <Stack gap={2}>
          {data.map((row) => (
            <CursorRow key={`${row.lea_id}/${row.partner}`} row={row} />
          ))}
        </Stack>
      )}
    </Box>
  );
}

function CursorRow({ row }: { row: CursorStateRow }) {
  const lag = row.days_behind ?? 0;
  const warn = lag > 20;
  const lastEventDisplay = row.last_event_at
    ? new Date(row.last_event_at).toLocaleString()
    : "Never";
  return (
    <Flex
      align="center"
      justify="space-between"
      p={3}
      bg={warn ? "red.50" : "gray.50"}
      borderRadius="md"
      title={
        row.last_event_id
          ? `Cursor position: ${row.last_event_id}`
          : undefined
      }
    >
      <Box>
        <Text fontSize="sm" fontWeight="medium">
          {labelForPartner(row.partner)}
        </Text>
        <Text fontSize="xs" color="gray.600">
          Last event: {lastEventDisplay}
        </Text>
      </Box>
      <Badge colorPalette={warn ? "red" : "green"} variant="subtle">
        {row.days_behind != null
          ? `${row.days_behind.toFixed(1)}d behind`
          : "n/a"}
      </Badge>
    </Flex>
  );
}

// ── Integrations (per-LEA per-partner) ───────────────────────────────────────

/**
 * Read-only integration summary for the LEA drawer.
 *
 * One card per ``(lea, partner)`` row in ``connector_authorization``
 * with ``revoked_at IS NULL``. Surfaces what's relevant when an
 * operator is on the LEA drawer triaging an issue: partner,
 * authorization status, EdLink-side integration status, sharing
 * scope, masked secret_ref, poll interval, who authorized it, when
 * the integration status was last observed.
 *
 * Mutations (rotate / revoke / adjust / re-authorize) deliberately
 * live on the Integrations page rather than here. The LEA can have
 * multiple partners in the future; a single "Rotate credential" on
 * the LEA drawer would be ambiguous. "Manage" deep-links to the
 * filtered Integrations view so the operator stays in flow.
 */
function IntegrationSection({ leaId }: { leaId: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ["connectors", leaId],
    queryFn: () => api.listConnectors({ lea_id: leaId }),
  });
  // Defensive client-side filter. The backend already filters via
  // the lea_id query param, but a stale dev server (running an
  // older route before the lea_id param landed) would return all
  // rows. This guard keeps the drawer from ever rendering another
  // LEA's integrations.
  const rows = (data ?? []).filter((r) => r.lea_id === leaId);
  return (
    <Box>
      <Flex align="baseline" justify="space-between" mb={2}>
        <Heading size="sm">Integrations</Heading>
        <ChakraLink
          href={`/integrations?lea=${encodeURIComponent(leaId)}`}
          fontSize="xs"
          color="blue.600"
          fontWeight="medium"
        >
          Manage →
        </ChakraLink>
      </Flex>
      {isLoading && <Skeleton height="100px" />}
      {!isLoading && rows.length === 0 && (
        <EmptyStateBlock
          title="No partner integration authorized"
          description="Authorize an EdLink (or future partner) integration from the Integrations page to start polling rosters."
        />
      )}
      {rows.length > 0 && (
        <Stack gap={2}>
          {rows.map((row) => (
            <IntegrationCard key={row.id} row={row} />
          ))}
        </Stack>
      )}
    </Box>
  );
}

const COMBINED_STATUS_BADGE_PALETTE: Record<string, string> = {
  ok: "green",
  stale: "orange",
  info: "blue",
  bad: "red",
  mute: "gray",
};

const COMBINED_STATUS_NOTE_COLOR: Record<string, string> = {
  bad: "red.600",
  info: "blue.600",
  mute: "gray.500",
  ok: "green.600",
  stale: "orange.600",
};

function IntegrationCard({ row }: { row: ConnectorAuthorizationOut }) {
  const view = combinedStatusView(row.status, row.integration_status);
  // Card background turns red only on a real partner-side degraded
  // signal. Steady-state and our-side terminal states (revoked /
  // locked) stay on the neutral gray surface so they do not shout
  // for attention.
  const noteIsDegraded =
    view.partnerNote != null && view.partnerNote.tone === "bad";
  return (
    <Box
      bg={noteIsDegraded ? "red.50" : "gray.50"}
      borderWidth="1px"
      borderColor={noteIsDegraded ? "red.200" : "gray.200"}
      borderRadius="md"
      p={3}
    >
      <Flex align="center" justify="space-between" gap={3} mb={2}>
        <HStack gap={2}>
          <Text fontSize="sm" fontWeight="medium">
            {labelForPartner(row.partner)}
          </Text>
          <Badge
            colorPalette={
              COMBINED_STATUS_BADGE_PALETTE[view.primary.tone] ?? "gray"
            }
            variant={noteIsDegraded ? "solid" : "subtle"}
          >
            {view.primary.label}
          </Badge>
          {view.partnerNote && (
            <Text
              fontSize="xs"
              color={
                COMBINED_STATUS_NOTE_COLOR[view.partnerNote.tone] ?? "gray.500"
              }
              title={
                row.integration_status_observed_at
                  ? `EdLink last observed ${new Date(
                      row.integration_status_observed_at,
                    ).toLocaleString()}`
                  : "EdLink integration not yet observed"
              }
            >
              {view.partnerNote.label}
            </Text>
          )}
        </HStack>
      </Flex>
      <Stack gap={1} fontSize="xs">
        <IntegrationMetaRow
          label="Sharing scope"
          value={
            row.sharing_scope ? labelForSharingScope(row.sharing_scope) : "—"
          }
        />
        <IntegrationMetaRow
          label="Poll interval"
          value={formatPollInterval(row.poll_interval_seconds)}
        />
        <IntegrationMetaRow
          label="Secret ref"
          value={compactIntegrationSecret(row.secret_ref)}
          mono
          title={row.secret_ref}
        />
        <IntegrationMetaRow
          label="Authorized by"
          value={row.authorized_by_email ?? "—"}
        />
        {row.authorized_at && (
          <IntegrationMetaRow
            label="Authorized"
            value={new Date(row.authorized_at).toLocaleString()}
          />
        )}
      </Stack>
    </Box>
  );
}

function IntegrationMetaRow({
  label,
  value,
  mono,
  title,
}: {
  label: string;
  value: string;
  mono?: boolean;
  title?: string;
}) {
  return (
    <Flex gap={3}>
      <Text minW="110px" color="gray.500">
        {label}
      </Text>
      <Text
        flex="1"
        fontFamily={mono ? "mono" : undefined}
        wordBreak="break-all"
        title={title}
      >
        {value}
      </Text>
    </Flex>
  );
}

function compactIntegrationSecret(value: string): string {
  if (value.length <= 28) return value;
  return `${value.slice(0, 16)}…${value.slice(-8)}`;
}

// ── Syncs ────────────────────────────────────────────────────────────────────

function SyncsSection({
  leaId,
  onAction,
  onDetails,
}: {
  leaId: string;
  onAction: (action: PendingAction) => void;
  onDetails: (sync: SyncJobSummary) => void;
}) {
  const { data, isLoading } = useQuery({
    queryKey: ["syncs", leaId],
    queryFn: () => api.listSyncs(leaId),
  });
  return (
    <Box>
      <Heading size="sm" mb={2}>
        Sync timeline
      </Heading>
      {isLoading && (
        <Stack gap={2}>
          <Skeleton height="40px" />
          <Skeleton height="40px" />
        </Stack>
      )}
      {data && data.length === 0 && (
        <EmptyStateBlock
          title="No syncs yet"
          description="Run the demo or seed-dev to populate state."
        />
      )}
      {data && data.length > 0 && (
        <Table.Root variant="line" size="sm">
          <Table.Header bg="gray.50">
            <Table.Row>
              <Table.ColumnHeader>Started</Table.ColumnHeader>
              <Table.ColumnHeader width="110px">Status</Table.ColumnHeader>
              <Table.ColumnHeader width="80px" textAlign="end">
                Events
              </Table.ColumnHeader>
              <Table.ColumnHeader
                title="Where the partner event cursor sat before this sync, and where it moved to after. Retry rewinds to the before-cursor; Revert undoes snapshots written between the two."
              >
                Cursor →
              </Table.ColumnHeader>
              <Table.ColumnHeader width="220px" textAlign="end">
                Actions
              </Table.ColumnHeader>
            </Table.Row>
          </Table.Header>
          <Table.Body>
            {data.map((sync) => (
              <Fragment key={sync.id}>
                <SyncRow
                  sync={sync}
                  onAction={onAction}
                  onDetails={onDetails}
                />
                {sync.status === "failed" && sync.error_summary && (
                  <SyncErrorSubRow
                    summary={sync.error_summary}
                    errorCount={sync.error_count}
                  />
                )}
              </Fragment>
            ))}
          </Table.Body>
        </Table.Root>
      )}
    </Box>
  );
}

function SyncRow({
  sync,
  onAction,
  onDetails,
}: {
  sync: SyncJobSummary;
  onAction: (action: PendingAction) => void;
  onDetails: (sync: SyncJobSummary) => void;
}) {
  const color = STATUS_COLOR[sync.status] ?? "gray";
  // Layer 5 (threshold) failures are not data faults; the sync wrote
  // nothing and replaying the same batch with the same baseline
  // re-fails identically. The correct verbs (Accept / Reject) don't
  // exist yet, so disable Retry and Revert and explain in the tooltip.
  // Tracked as follow-up: L5 action plane.
  const isLayer5Failure = sync.error_summary?.includes("L5:") ?? false;
  const layer5Tooltip = isLayer5Failure
    ? "Disabled for Layer 5 (threshold) failures. The sync wrote no data and replaying re-fails on the same baseline. Accept / Reject controls for threshold breaches are not built yet."
    : undefined;
  return (
    <Table.Row>
      <Table.Cell>
        <Text fontSize="sm">{relativeTime(sync.started_at)}</Text>
        <Text
          fontSize="xs"
          color="gray.500"
          fontFamily="mono"
          title={sync.id}
        >
          {sync.id.slice(0, 8)}
        </Text>
      </Table.Cell>
      <Table.Cell>
        <Badge colorPalette={color} variant="subtle">
          {labelForSyncStatus(sync.status)}
        </Badge>
      </Table.Cell>
      <Table.Cell textAlign="end" fontVariantNumeric="tabular-nums">
        <Text fontSize="sm">{sync.event_count}</Text>
      </Table.Cell>
      <Table.Cell>
        <CursorTransition
          before={sync.cursor_before}
          after={sync.cursor_after}
        />
      </Table.Cell>
      <Table.Cell textAlign="end">
        <HStack gap={2} justify="flex-end">
          <Button
            size="xs"
            variant="ghost"
            onClick={() => onDetails(sync)}
          >
            Details
          </Button>
          <Button
            size="xs"
            variant="outline"
            onClick={() => onAction({ kind: "retry", sync })}
            disabled={sync.status === "revert" || isLayer5Failure}
            title={layer5Tooltip}
          >
            Retry
          </Button>
          <Button
            size="xs"
            variant="outline"
            colorPalette="purple"
            onClick={() => onAction({ kind: "revert", sync })}
            disabled={
              sync.status === "revert" ||
              sync.status === "quarantine_release" ||
              isLayer5Failure
            }
            title={layer5Tooltip}
          >
            Revert
          </Button>
        </HStack>
      </Table.Cell>
    </Table.Row>
  );
}

function SyncErrorSubRow({
  summary,
  errorCount,
}: {
  summary: string;
  errorCount: number;
}) {
  return (
    <Table.Row bg="red.50">
      <Table.Cell colSpan={5} py={2} borderColor="red.100">
        <Flex align="center" gap={2}>
          <Text
            fontSize="xs"
            color="red.700"
            title={summary}
            flex="1"
            minW={0}
          >
            {summarizeErrorSummary(summary)}
          </Text>
          {errorCount > 0 && (
            <Text
              fontSize="xs"
              color="red.700"
              fontVariantNumeric="tabular-nums"
              flexShrink={0}
            >
              {errorCount} error{errorCount === 1 ? "" : "s"}
            </Text>
          )}
        </Flex>
      </Table.Cell>
    </Table.Row>
  );
}

// ── Sync detail dialog ──────────────────────────────────────────────────────

const LAYER_COLOR: Record<number, string> = {
  1: "red",
  2: "orange",
  3: "yellow",
  4: "purple",
  5: "blue",
};

function SyncDetailBody({ sync }: { sync: SyncJobSummary }) {
  // The detail endpoint returns validation issues, quarantine ids,
  // and retry/revert history. Wire-format event payloads are not
  // stored: the backend keeps only the event id reference per issue
  // (payload_reference). When raw payload capture lands, surface it
  // here next to each issue.
  const { data, isLoading, isError } = useQuery({
    queryKey: ["syncs", "detail", sync.id],
    queryFn: () => api.getSync(sync.id),
  });
  return (
    <Stack gap={5}>
      <SyncMetadataBlock sync={sync} />
      {isLoading && <Skeleton height="80px" />}
      {isError && (
        <Text fontSize="sm" color="red.600">
          Could not load sync detail. The admin API may be restarting;
          try again in a moment.
        </Text>
      )}
      {data && (
        <>
          <ValidationIssuesBlock issues={data.validation_issues} />
          <QuarantineRefsBlock entityIds={data.quarantined_entity_ids} />
          <RetryHistoryBlock history={data.retry_history} />
          <RevertHistoryBlock history={data.revert_history} />
        </>
      )}
    </Stack>
  );
}

function SyncMetadataBlock({ sync }: { sync: SyncJobSummary }) {
  return (
    <Box>
      <Heading size="xs" mb={2} color="gray.600" textTransform="uppercase">
        Sync metadata
      </Heading>
      <Stack gap={1} fontSize="sm">
        <MetadataRow label="ID" value={sync.id} mono />
        <MetadataRow label="Partner" value={labelForPartner(sync.partner)} />
        <MetadataRow label="Status" value={labelForSyncStatus(sync.status)} />
        <MetadataRow
          label="Started"
          value={new Date(sync.started_at).toLocaleString()}
        />
        <MetadataRow
          label="Completed"
          value={
            sync.completed_at
              ? new Date(sync.completed_at).toLocaleString()
              : "—"
          }
        />
        <MetadataRow label="Events" value={String(sync.event_count)} />
        <MetadataRow label="Errors" value={String(sync.error_count)} />
        <MetadataRow label="Warnings" value={String(sync.warning_count)} />
        <MetadataRow
          label="Cursor before"
          value={sync.cursor_before ?? "—"}
          mono
        />
        <MetadataRow
          label="Cursor after"
          value={sync.cursor_after ?? "—"}
          mono
        />
        {sync.error_summary && (
          <MetadataRow label="Error summary" value={sync.error_summary} mono />
        )}
      </Stack>
    </Box>
  );
}

function MetadataRow({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <Flex gap={3}>
      <Text minW="130px" color="gray.500">
        {label}
      </Text>
      <Text
        flex="1"
        fontFamily={mono ? "mono" : undefined}
        wordBreak="break-all"
      >
        {value}
      </Text>
    </Flex>
  );
}

function ValidationIssuesBlock({ issues }: { issues: ValidationIssueRow[] }) {
  return (
    <Box>
      <Heading size="xs" mb={2} color="gray.600" textTransform="uppercase">
        Validation issues ({issues.length})
      </Heading>
      {issues.length === 0 ? (
        <Text fontSize="sm" color="gray.500">
          No validation issues recorded for this sync.
        </Text>
      ) : (
        <Stack gap={2}>
          {issues.map((issue, idx) => (
            <ValidationIssueCard key={idx} issue={issue} />
          ))}
        </Stack>
      )}
    </Box>
  );
}

function ValidationIssueCard({ issue }: { issue: ValidationIssueRow }) {
  const layerColor = LAYER_COLOR[issue.layer] ?? "gray";
  const hasDetail =
    issue.detail !== null &&
    typeof issue.detail === "object" &&
    Object.keys(issue.detail).length > 0;
  return (
    <Box
      borderWidth="1px"
      borderColor="gray.200"
      borderRadius="md"
      p={2.5}
      bg="white"
    >
      <Flex align="center" gap={2} flexWrap="wrap">
        <Badge colorPalette={layerColor} variant="subtle">
          Layer {issue.layer}
        </Badge>
        <Text fontSize="sm" fontWeight="medium" title={issue.code}>
          {labelForErrorCode(issue.code)}
        </Text>
        {issue.payload_reference && (
          <Code fontSize="xs" colorPalette="gray">
            {issue.payload_reference}
          </Code>
        )}
        <Text fontSize="xs" color="gray.500" ml="auto">
          {new Date(issue.created_at).toLocaleString()}
        </Text>
      </Flex>
      {hasDetail && (
        <Box
          mt={2}
          p={2}
          bg="gray.50"
          borderRadius="sm"
          fontFamily="mono"
          fontSize="xs"
          color="gray.800"
          whiteSpace="pre-wrap"
          wordBreak="break-all"
        >
          {JSON.stringify(issue.detail, null, 2)}
        </Box>
      )}
    </Box>
  );
}

function QuarantineRefsBlock({ entityIds }: { entityIds: string[] }) {
  if (entityIds.length === 0) return null;
  return (
    <Box>
      <Heading size="xs" mb={2} color="gray.600" textTransform="uppercase">
        Quarantined entities ({entityIds.length})
      </Heading>
      <Stack gap={1}>
        {entityIds.map((id) => (
          <Code key={id} fontSize="xs">
            {id}
          </Code>
        ))}
      </Stack>
    </Box>
  );
}

function RetryHistoryBlock({
  history,
}: {
  history: { id: string; operator_identity: string; reason: string; retried_at: string; cursor_rewound_to: string | null; forced: boolean }[];
}) {
  if (history.length === 0) return null;
  return (
    <Box>
      <Heading size="xs" mb={2} color="gray.600" textTransform="uppercase">
        Retry history ({history.length})
      </Heading>
      <Stack gap={2}>
        {history.map((row) => (
          <Box
            key={row.id}
            borderWidth="1px"
            borderColor="blue.100"
            borderRadius="md"
            p={2}
            bg="blue.50"
            fontSize="xs"
          >
            <Flex justify="space-between" gap={2}>
              <Text fontWeight="medium">{row.operator_identity}</Text>
              <Text color="gray.600">
                {new Date(row.retried_at).toLocaleString()}
              </Text>
            </Flex>
            <Text mt={1}>{row.reason}</Text>
            <Flex gap={2} mt={1} color="gray.600">
              {row.cursor_rewound_to && (
                <Text fontFamily="mono">
                  Rewound to {row.cursor_rewound_to}
                </Text>
              )}
              {row.forced && (
                <Badge colorPalette="orange" variant="subtle">
                  Forced
                </Badge>
              )}
            </Flex>
          </Box>
        ))}
      </Stack>
    </Box>
  );
}

function RevertHistoryBlock({
  history,
}: {
  history: { id: string; operator_identity: string; reason: string; reverted_at: string; snapshots_restored: number }[];
}) {
  if (history.length === 0) return null;
  return (
    <Box>
      <Heading size="xs" mb={2} color="gray.600" textTransform="uppercase">
        Revert history ({history.length})
      </Heading>
      <Stack gap={2}>
        {history.map((row) => (
          <Box
            key={row.id}
            borderWidth="1px"
            borderColor="purple.100"
            borderRadius="md"
            p={2}
            bg="purple.50"
            fontSize="xs"
          >
            <Flex justify="space-between" gap={2}>
              <Text fontWeight="medium">{row.operator_identity}</Text>
              <Text color="gray.600">
                {new Date(row.reverted_at).toLocaleString()}
              </Text>
            </Flex>
            <Text mt={1}>{row.reason}</Text>
            <Text mt={1} color="gray.600">
              {row.snapshots_restored} snapshot
              {row.snapshots_restored === 1 ? "" : "s"} restored
            </Text>
          </Box>
        ))}
      </Stack>
    </Box>
  );
}

function CursorTransition({
  before,
  after,
}: {
  before: string | null;
  after: string | null;
}) {
  const tooltipText =
    `Before: ${before ?? "(empty)"}\nAfter: ${after ?? "(empty)"}`;
  return (
    <HStack
      gap={1.5}
      fontSize="xs"
      fontFamily="mono"
      color="gray.700"
      title={tooltipText}
    >
      <Text>{compact(before)}</Text>
      <Text color="gray.400">→</Text>
      <Text>{compact(after)}</Text>
    </HStack>
  );
}

function compact(cursor: string | null): string {
  if (!cursor) return "—";
  if (cursor.length <= 14) return cursor;
  return `${cursor.slice(0, 6)}…${cursor.slice(-4)}`;
}

function relativeTime(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  const minutes = ms / 60_000;
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${Math.round(minutes)}m ago`;
  const hours = minutes / 60;
  if (hours < 24) return `${Math.round(hours)}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

// ── Reconciliation drift ─────────────────────────────────────────────────────

const RECONCILIATION_STATUS_COLOR: Record<string, string> = {
  matched: "green",
  drift_detected: "orange",
  skipped_quiet_window: "gray",
  failed: "red",
};

/**
 * Reconciliation drift section for the LEA drawer.
 *
 * Three layers:
 *
 * 1. Latest run card: status badge, partner, completed-at, root hashes.
 * 2. Drift summary chips: one chip per entity-type with canonical-only
 *    vs partner-only counts so the operator sees the breadth of drift
 *    at a glance.
 * 3. Per-entity-type breakdown: canonical-only and partner-only id
 *    samples so the operator can drill into the affected rows.
 * 4. History tail: prior runs in a compact table.
 *
 * "Matched" or "skipped_quiet_window" runs show only the latest card
 * plus the history tail; the summary chips and breakdown only render
 * when ``drift.length > 0`` so the section stays quiet when there is
 * no signal.
 */
function ReconciliationDriftSection({ leaId }: { leaId: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ["reconciliation", leaId],
    queryFn: () => api.listReconciliationRuns(leaId),
  });
  return (
    <Box>
      <Heading size="sm" mb={2}>
        Reconciliation drift
      </Heading>
      {isLoading && (
        <Stack gap={2}>
          <Skeleton height="60px" />
          <Skeleton height="40px" />
        </Stack>
      )}
      {!isLoading && (!data || data.length === 0) && (
        <EmptyStateBlock
          title="No reconciliations run yet"
          description="The daily sweep runs at 02:00 LEA-local. Run `edlink-rostering reconcile` from the CLI to force one now."
        />
      )}
      {data && data.length > 0 && (
        <Stack gap={3}>
          <LatestReconciliation run={data[0]} />
          {data[0].drift.length > 0 && (
            <DriftEntityBreakdown drift={data[0].drift} />
          )}
          {data.length > 1 && <ReconciliationHistoryTable runs={data.slice(1)} />}
        </Stack>
      )}
    </Box>
  );
}

function LatestReconciliation({ run }: { run: ReconciliationRunRow }) {
  const color = RECONCILIATION_STATUS_COLOR[run.status] ?? "gray";
  const totalDriftIds = run.drift.reduce(
    (sum, d) => sum + d.canonical_only_ids.length + d.partner_only_ids.length,
    0,
  );
  return (
    <Box
      bg={run.status === "drift_detected" ? "orange.50" : "gray.50"}
      borderWidth="1px"
      borderColor={
        run.status === "drift_detected" ? "orange.200" : "gray.200"
      }
      borderRadius="md"
      p={3}
    >
      <Flex align="center" justify="space-between" gap={3}>
        <Box>
          <HStack gap={2}>
            <Text fontSize="sm" fontWeight="medium">
              Latest run
            </Text>
            <Badge colorPalette={color} variant="subtle">
              {labelForReconciliationStatus(run.status)}
            </Badge>
          </HStack>
          <Text fontSize="xs" color="gray.600">
            {relativeTime(run.completed_at)} ·{" "}
            {labelForPartner(run.partner)}
          </Text>
        </Box>
        <HStack
          gap={1.5}
          fontSize="xs"
          fontFamily="mono"
          color="gray.700"
          title={`canonical: ${run.canonical_root_hash}\npartner: ${run.partner_root_hash ?? "(skipped)"}`}
        >
          <Text>{compact(run.canonical_root_hash)}</Text>
          <Text color="gray.400">vs</Text>
          <Text>{compact(run.partner_root_hash)}</Text>
        </HStack>
      </Flex>
      {run.drift.length > 0 && (
        <Flex gap={2} flexWrap="wrap" mt={3}>
          {run.drift.map((d) => (
            <DriftSummaryChip key={d.entity_type} drift={d} />
          ))}
          <Badge
            colorPalette="orange"
            variant="solid"
            title={`${totalDriftIds} divergent ids across ${run.drift.length} entity types`}
          >
            {totalDriftIds} total
          </Badge>
        </Flex>
      )}
      {run.error_message && (
        <Text fontSize="xs" color="red.600" mt={1}>
          {run.error_message}
        </Text>
      )}
    </Box>
  );
}

function DriftSummaryChip({
  drift,
}: {
  drift: ReconciliationRunRow["drift"][number];
}) {
  const canonicalOnly = drift.canonical_only_ids.length;
  const partnerOnly = drift.partner_only_ids.length;
  const total = canonicalOnly + partnerOnly;
  return (
    <Badge
      colorPalette="orange"
      variant="subtle"
      title={`${labelForEntityType(drift.entity_type)}: ${canonicalOnly} canonical-only, ${partnerOnly} partner-only`}
    >
      {labelForEntityType(drift.entity_type)} · {total}
    </Badge>
  );
}

function DriftEntityBreakdown({
  drift,
}: {
  drift: ReconciliationRunRow["drift"];
}) {
  return (
    <Stack gap={2}>
      {drift.map((d) => (
        <DriftEntityCard key={d.entity_type} drift={d} />
      ))}
    </Stack>
  );
}

function DriftEntityCard({
  drift,
}: {
  drift: ReconciliationRunRow["drift"][number];
}) {
  return (
    <Box
      borderWidth="1px"
      borderColor="gray.200"
      borderRadius="md"
      p={2.5}
      bg="white"
    >
      <Flex align="center" gap={2} mb={1.5}>
        <Text fontSize="sm" fontWeight="medium">
          {labelForEntityType(drift.entity_type)}
        </Text>
        <Text
          fontSize="xs"
          color="gray.500"
          fontFamily="mono"
          title={`canonical mid: ${drift.canonical_mid_hash}\npartner mid: ${drift.partner_mid_hash}`}
        >
          {compact(drift.canonical_mid_hash)} ≠ {compact(drift.partner_mid_hash)}
        </Text>
      </Flex>
      <Stack gap={1}>
        <DriftIdRow
          label="Canonical-only"
          ids={drift.canonical_only_ids}
          tone="blue"
        />
        <DriftIdRow
          label="Partner-only"
          ids={drift.partner_only_ids}
          tone="purple"
        />
      </Stack>
    </Box>
  );
}

function DriftIdRow({
  label,
  ids,
  tone,
}: {
  label: string;
  ids: string[];
  tone: string;
}) {
  return (
    <Flex gap={2} fontSize="xs">
      <Badge
        colorPalette={tone}
        variant="subtle"
        minW="100px"
        textAlign="center"
      >
        {label} · {ids.length}
      </Badge>
      <Text
        color="gray.700"
        fontFamily="mono"
        flex="1"
        title={ids.join(", ")}
        lineClamp={1}
      >
        {ids.slice(0, 5).join(", ") || "—"}
        {ids.length > 5 ? ` (+${ids.length - 5} more)` : ""}
      </Text>
    </Flex>
  );
}

function ReconciliationHistoryTable({
  runs,
}: {
  runs: ReconciliationRunRow[];
}) {
  return (
    <Table.Root variant="line" size="sm">
      <Table.Header bg="gray.50">
        <Table.Row>
          <Table.ColumnHeader>Completed</Table.ColumnHeader>
          <Table.ColumnHeader width="150px">Status</Table.ColumnHeader>
          <Table.ColumnHeader width="80px" textAlign="end">
            Drift types
          </Table.ColumnHeader>
        </Table.Row>
      </Table.Header>
      <Table.Body>
        {runs.map((run) => (
          <Table.Row key={run.id}>
            <Table.Cell>
              <Text fontSize="sm">{relativeTime(run.completed_at)}</Text>
              <Text
                fontSize="xs"
                color="gray.500"
                fontFamily="mono"
                title={run.id}
              >
                {run.id.slice(0, 8)}
              </Text>
            </Table.Cell>
            <Table.Cell>
              <Badge
                colorPalette={
                  RECONCILIATION_STATUS_COLOR[run.status] ?? "gray"
                }
                variant="subtle"
              >
                {labelForReconciliationStatus(run.status)}
              </Badge>
            </Table.Cell>
            <Table.Cell textAlign="end" fontVariantNumeric="tabular-nums">
              <Text fontSize="sm">{run.drift.length}</Text>
            </Table.Cell>
          </Table.Row>
        ))}
      </Table.Body>
    </Table.Root>
  );
}

// ── Quarantine ───────────────────────────────────────────────────────────────

export function QuarantineSection({
  leaId,
  onReject,
}: {
  leaId: string;
  onReject: (row: QuarantineRowOut) => void;
}) {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["quarantine", leaId],
    queryFn: () => api.listQuarantine(leaId),
  });
  const releaseMutation = useMutation({
    mutationFn: (id: string) => api.releaseQuarantine(id),
    onMutate: async (id) => {
      // Optimistic remove. If the server refuses, onError restores
      // the row and shows a toast.
      await qc.cancelQueries({ queryKey: ["quarantine", leaId] });
      const previous = qc.getQueryData<QuarantineRowOut[]>([
        "quarantine",
        leaId,
      ]);
      qc.setQueryData<QuarantineRowOut[]>(
        ["quarantine", leaId],
        (rows) => (rows ?? []).filter((r) => r.id !== id),
      );
      return { previous };
    },
    onSuccess: () => {
      notifySuccess(
        "Quarantine released",
        "Row applied to canonical; the audit timeline shows the synthetic sync_job.",
      );
    },
    onError: (err, _id, ctx) => {
      if (ctx?.previous !== undefined) {
        qc.setQueryData(["quarantine", leaId], ctx.previous);
      }
      notifyError(
        "Release refused",
        err instanceof Error ? err.message : String(err),
      );
    },
    onSettled: () => {
      invalidateLea(qc, leaId);
    },
  });
  return (
    <Box>
      <Heading size="sm" mb={2}>
        Quarantine queue
      </Heading>
      {!data || data.length === 0 ? (
        <EmptyStateBlock
          title="No quarantined rows for this LEA"
          description="Layer 4 orphan rows land here. Empty means all referential constraints are clean."
        />
      ) : (
        <Stack gap={2}>
          {data.map((row) => (
            <QuarantineCard
              key={row.id}
              row={row}
              onRelease={() => releaseMutation.mutate(row.id)}
              onReject={() => onReject(row)}
            />
          ))}
        </Stack>
      )}
    </Box>
  );
}

function QuarantineCard({
  row,
  onRelease,
  onReject,
}: {
  row: QuarantineRowOut;
  onRelease: () => void;
  onReject: () => void;
}) {
  return (
    <Box
      bg="orange.50"
      borderWidth="1px"
      borderColor="orange.200"
      borderRadius="md"
      p={3}
    >
      <Flex align="center" justify="space-between" gap={3}>
        <Box>
          <Text fontSize="sm" fontWeight="medium">
            {row.entity_type}/{row.entity_id}
          </Text>
          <Text fontSize="xs" color="gray.600">
            {row.reason}
          </Text>
          <Text fontSize="xs" color="gray.500">
            {new Date(row.created_at).toLocaleString()}
          </Text>
        </Box>
        <HStack gap={2}>
          <Button size="xs" colorPalette="green" onClick={onRelease}>
            Release
          </Button>
          <Button size="xs" variant="outline" onClick={onReject}>
            Reject
          </Button>
        </HStack>
      </Flex>
    </Box>
  );
}

// ── Activity timeline ────────────────────────────────────────────────────────

function AdminTimelineSection({ leaId }: { leaId: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ["timeline", leaId],
    queryFn: () => api.listLeaTimeline(leaId),
  });
  return (
    <Box>
      <Heading size="sm" mb={2}>
        Activity timeline
      </Heading>
      <Text fontSize="xs" color="gray.500" mb={2}>
        Every sync, retry, revert, quarantine state change,
        reconciliation, and admin action on this LEA in one view.
      </Text>
      {isLoading && (
        <Stack gap={2}>
          <Skeleton height="44px" />
          <Skeleton height="44px" />
          <Skeleton height="44px" />
        </Stack>
      )}
      {!isLoading && (!data || data.length === 0) && (
        <EmptyStateBlock
          title="No activity yet"
          description="Run the demo or trigger a sync to populate the timeline."
        />
      )}
      {data && data.length > 0 && (
        <Stack gap={1.5}>
          {data.map((entry) => (
            <TimelineRow key={entry.id} entry={entry} />
          ))}
        </Stack>
      )}
    </Box>
  );
}

function TimelineRow({ entry }: { entry: TimelineEntryOut }) {
  const sourceColor = colorForTimelineSource(entry.source);
  const actorDisplay =
    entry.actor_kind === "system"
      ? "System"
      : entry.actor_email ?? "Operator";
  return (
    <Flex
      align="flex-start"
      gap={3}
      p={2.5}
      borderWidth="1px"
      borderColor="gray.200"
      borderRadius="md"
      bg="white"
    >
      <Badge
        colorPalette={sourceColor}
        variant="subtle"
        minW="120px"
        textAlign="center"
        flexShrink={0}
      >
        {labelForTimelineSource(entry.source)}
      </Badge>
      <Box flex="1" minW={0}>
        <Flex align="baseline" justify="space-between" gap={3}>
          <Text fontSize="sm" fontWeight="medium" title={entry.action}>
            {labelForTimelineAction(entry.action)}
          </Text>
          <Text
            fontSize="xs"
            color="gray.500"
            flexShrink={0}
            title={new Date(entry.occurred_at).toLocaleString()}
          >
            {relativeTime(entry.occurred_at)}
          </Text>
        </Flex>
        <Text fontSize="xs" color="gray.600" mt={0.5}>
          {actorDisplay}
        </Text>
        {entry.reason && (
          <Text
            fontSize="xs"
            color="gray.700"
            mt={1}
            title={entry.reason}
            lineClamp={2}
          >
            {entry.reason}
          </Text>
        )}
        {entry.detail && (
          <TimelineDetailLine detail={entry.detail} />
        )}
      </Box>
    </Flex>
  );
}

function TimelineDetailLine({
  detail,
}: {
  detail: Record<string, unknown>;
}) {
  // Render the two or three most operator-useful keys for the
  // common sources; the full payload is reachable via the title.
  const pieces: string[] = [];
  if (typeof detail.partner === "string") {
    pieces.push(labelForPartner(detail.partner));
  }
  if (typeof detail.entity_type === "string" && typeof detail.entity_id === "string") {
    pieces.push(`${detail.entity_type}/${detail.entity_id}`);
  }
  if (typeof detail.event_count === "number") {
    pieces.push(`${detail.event_count} event${detail.event_count === 1 ? "" : "s"}`);
  }
  if (typeof detail.snapshots_restored === "number") {
    pieces.push(
      `${detail.snapshots_restored} snapshot${detail.snapshots_restored === 1 ? "" : "s"} restored`,
    );
  }
  if (typeof detail.drift_count === "number" && detail.drift_count > 0) {
    pieces.push(`${detail.drift_count} drift entry`);
  }
  if (detail.forced === true) {
    pieces.push("forced");
  }
  if (pieces.length === 0) return null;
  return (
    <Text
      fontSize="xs"
      color="gray.600"
      mt={1}
      title={JSON.stringify(detail, null, 2)}
    >
      {pieces.join(" · ")}
    </Text>
  );
}

// ── Shared empty state ───────────────────────────────────────────────────────

function EmptyStateBlock({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <EmptyState.Root size="sm">
      <EmptyState.Content>
        <EmptyState.Title fontSize="sm">{title}</EmptyState.Title>
        <EmptyState.Description fontSize="xs" color="gray.500">
          {description}
        </EmptyState.Description>
      </EmptyState.Content>
    </EmptyState.Root>
  );
}

function invalidateLea(qc: QueryClient, leaId: string): void {
  qc.invalidateQueries({ queryKey: ["leas"] });
  qc.invalidateQueries({ queryKey: ["syncs", leaId] });
  qc.invalidateQueries({ queryKey: ["cursors", leaId] });
  qc.invalidateQueries({ queryKey: ["cursors"] });
  qc.invalidateQueries({ queryKey: ["quarantine", leaId] });
  qc.invalidateQueries({ queryKey: ["quarantine"] });
  qc.invalidateQueries({ queryKey: ["reconciliation", leaId] });
  qc.invalidateQueries({ queryKey: ["timeline", leaId] });
  qc.invalidateQueries({ queryKey: ["alerts"] });
}
