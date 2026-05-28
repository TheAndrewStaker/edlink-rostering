/**
 * "Send test event" menu, dev-profile only.
 *
 * Lives in the LEA detail drawer header so a live walkthrough can poke
 * one LEA at a time without leaving the LEA's context. Section
 * dividers group nine scenarios into Happy path / Validation failures
 * / Threshold alerts / Other so the menu reads as a guided tour of the
 * validation pipeline.
 *
 * The menu only renders when `import.meta.env.DEV` is true. The
 * underlying endpoint also 404s outside `EDLINK_PROFILE=dev`, so a
 * deployed dev bundle pointed at a prod API degrades gracefully.
 *
 * On click: dispatch fires, a toast confirms receipt, and the LEA's
 * queries invalidate so the running pill and the in-flight KPI light
 * up on the next React Query poll cycle (5s staleTime + 10s refetch).
 */

import {
  Badge,
  Box,
  Button,
  HStack,
  Menu,
  Portal,
  Spinner,
  Text,
} from "@chakra-ui/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ApiError, type TestEventScenario } from "@/api/client";
import { notifyError, notifySuccess } from "@/lib/notify";

interface Props {
  leaId: string;
}

const SECTION_LABELS: Record<string, string> = {
  happy: "Happy path",
  validation: "Validation failures",
  thresholds: "Threshold alerts",
  other: "Other",
};

const SECTION_ORDER: string[] = ["happy", "validation", "thresholds", "other"];

const SECTION_PALETTE: Record<string, string> = {
  happy: "green",
  validation: "orange",
  thresholds: "red",
  other: "purple",
};

export function SendTestEventMenu({ leaId }: Props) {
  const qc = useQueryClient();
  const catalog = useQuery({
    queryKey: ["dev", "test-event-scenarios"],
    queryFn: api.listTestEventScenarios,
    enabled: import.meta.env.DEV,
    staleTime: 60_000,
    retry: false,
  });

  const dispatch = useMutation({
    mutationFn: ({
      scenarioId,
      label,
    }: {
      scenarioId: string;
      label: string;
    }) =>
      api.dispatchTestEvent(leaId, scenarioId).then((result) => ({
        ...result,
        label,
      })),
    onSuccess: (result) => {
      notifySuccess(
        "Test event sent",
        `${result.label}. Sync running on this LEA; the pill and KPI light up within a few seconds.`,
      );
      qc.invalidateQueries({ queryKey: ["leas"] });
      qc.invalidateQueries({ queryKey: ["syncs", leaId] });
      qc.invalidateQueries({ queryKey: ["timeline", leaId] });
      qc.invalidateQueries({ queryKey: ["reconciliation", leaId] });
      qc.invalidateQueries({ queryKey: ["quarantine"] });
      qc.invalidateQueries({ queryKey: ["alerts"] });
    },
    onError: (err) => {
      notifyError(
        "Send test event failed",
        err instanceof Error ? err.message : String(err),
      );
    },
  });

  if (!import.meta.env.DEV) {
    return null;
  }

  // 404 = dev profile off; 403 = role lacks permission (e.g. operator).
  // Hide the menu entirely rather than showing an error.
  if (
    catalog.error instanceof ApiError &&
    (catalog.error.status === 404 || catalog.error.status === 403)
  ) {
    return null;
  }

  const scenarios = catalog.data?.scenarios ?? [];
  const grouped = groupBySection(scenarios);
  const populatedSections = SECTION_ORDER.filter(
    (section) => (grouped[section] ?? []).length > 0,
  );

  return (
    <Menu.Root>
      <Menu.Trigger asChild>
        <Button
          size="sm"
          variant="outline"
          colorPalette="blue"
          loading={dispatch.isPending}
        >
          Send test event
        </Button>
      </Menu.Trigger>
      <Portal>
        <Menu.Positioner>
          <Menu.Content minW="340px">
            <MenuStateOrItems
              isLoading={catalog.isLoading}
              error={catalog.error}
              onRetry={() => void catalog.refetch()}
              populatedSections={populatedSections}
              grouped={grouped}
              onDispatch={(scenario) =>
                dispatch.mutate({
                  scenarioId: scenario.id,
                  label: scenario.label,
                })
              }
            />
          </Menu.Content>
        </Menu.Positioner>
      </Portal>
    </Menu.Root>
  );
}

function MenuStateOrItems({
  isLoading,
  error,
  onRetry,
  populatedSections,
  grouped,
  onDispatch,
}: {
  isLoading: boolean;
  error: unknown;
  onRetry: () => void;
  populatedSections: string[];
  grouped: Record<string, TestEventScenario[]>;
  onDispatch: (scenario: TestEventScenario) => void;
}) {
  if (isLoading) {
    return (
      <Box px={3} py={4}>
        <HStack gap={2}>
          <Spinner size="sm" />
          <Text fontSize="sm" color="gray.600">
            Loading scenarios…
          </Text>
        </HStack>
      </Box>
    );
  }
  if (error) {
    const message = error instanceof Error ? error.message : String(error);
    return (
      <Box px={3} py={3}>
        <Text fontSize="sm" color="red.600" mb={2}>
          Could not load test-event scenarios.
        </Text>
        <Text fontSize="xs" color="gray.600" mb={2} title={message}>
          {message}
        </Text>
        <Button size="xs" variant="outline" onClick={onRetry}>
          Retry
        </Button>
      </Box>
    );
  }
  if (populatedSections.length === 0) {
    return (
      <Box px={3} py={4}>
        <Text fontSize="sm" color="gray.600">
          No test-event scenarios available. Confirm the dev profile is
          on and that scenario fixtures are loaded.
        </Text>
      </Box>
    );
  }
  return (
    <>
      {populatedSections.map((section) => (
        <Menu.ItemGroup key={section}>
          <Menu.ItemGroupLabel>
            <HStack gap={2}>
              <Badge
                colorPalette={SECTION_PALETTE[section] ?? "gray"}
                variant="subtle"
                fontSize="2xs"
              >
                {SECTION_LABELS[section] ?? section}
              </Badge>
            </HStack>
          </Menu.ItemGroupLabel>
          {(grouped[section] ?? []).map((scenario) => (
            <Menu.Item
              key={scenario.id}
              value={scenario.id}
              onClick={() => onDispatch(scenario)}
            >
              <Box flex={1}>
                <Text fontSize="sm" fontWeight="medium">
                  {scenario.label}
                </Text>
                <Text fontSize="xs" color="gray.500" lineClamp={2}>
                  {scenario.description}
                </Text>
              </Box>
            </Menu.Item>
          ))}
        </Menu.ItemGroup>
      ))}
    </>
  );
}

function groupBySection(
  scenarios: TestEventScenario[],
): Record<string, TestEventScenario[]> {
  const result: Record<string, TestEventScenario[]> = {};
  for (const scenario of scenarios) {
    const list = result[scenario.section] ?? [];
    list.push(scenario);
    result[scenario.section] = list;
  }
  return result;
}
