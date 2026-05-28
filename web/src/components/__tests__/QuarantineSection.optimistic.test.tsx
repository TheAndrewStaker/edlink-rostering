/**
 * Optimistic rollback test for the quarantine release mutation.
 *
 * The mutation in LeaDetailPanel's QuarantineSection removes the row
 * from the cached list on `onMutate` so the panel reflects the action
 * before the server round-trip resolves. If the server rejects, the
 * snapshot restores the row and the toaster fires an error notice.
 *
 * This test seeds the QueryClient with a single quarantine row, makes
 * the API return 500, clicks Release, and asserts both:
 *   1. The cache reverts to the seeded shape (the row reappears).
 *   2. The QueryClient state is consistent with rollback.
 */

import { HttpResponse, http } from "msw";
import { userEvent } from "@testing-library/user-event";
import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { QuarantineSection } from "@/components/LeaDetailPanel";
import type { QuarantineRowOut } from "@/api/client";
import { server } from "@/mocks/server";
import {
  makeQueryClient,
  renderWithProviders,
} from "@/__tests__/renderWithProviders";

const LEA_ID = "lea-test-001";
const QUARANTINE_ID = "q-test-001";

function seedRow(): QuarantineRowOut {
  return {
    id: QUARANTINE_ID,
    sync_job_id: "sj-test-001",
    lea_id: LEA_ID,
    entity_type: "enrollment",
    entity_id: "enr-test-001",
    reason: "ENROLLMENT_ORPHAN_STUDENT",
    created_at: new Date().toISOString(),
    resolved_at: null,
    resolution_status: null,
    resolution_operator: null,
  };
}

describe("QuarantineSection optimistic release", () => {
  it("rolls the row back into the list when the server rejects", async () => {
    const queryClient = makeQueryClient();
    const seeded = seedRow();
    queryClient.setQueryData<QuarantineRowOut[]>(
      ["quarantine", LEA_ID],
      [seeded],
    );

    server.use(
      http.post(`/api/v1/quarantine/${QUARANTINE_ID}/release`, () =>
        HttpResponse.json({ detail: "release refused" }, { status: 500 }),
      ),
      http.get("/api/v1/quarantine", () => HttpResponse.json([seeded])),
    );

    renderWithProviders(
      <QuarantineSection leaId={LEA_ID} onReject={vi.fn()} />,
      { queryClient },
    );

    const releaseButton = await screen.findByRole("button", {
      name: "Release",
    });
    const user = userEvent.setup();
    await user.click(releaseButton);

    await waitFor(() => {
      const current = queryClient.getQueryData<QuarantineRowOut[]>([
        "quarantine",
        LEA_ID,
      ]);
      expect(current).toBeDefined();
      expect(current).toHaveLength(1);
      expect(current?.[0].id).toBe(QUARANTINE_ID);
    });

    const state = queryClient
      .getMutationCache()
      .getAll()
      .map((m) => m.state.status);
    expect(state).toContain("error");
  });

  it("removes the row from the cached list as soon as Release is clicked", async () => {
    // The optimistic-update half of the contract: the panel must
    // reflect the action immediately. Server response is irrelevant
    // for this assertion (the e2e layer owns the round-trip), but the
    // POST handler still needs to resolve so the mutation does not
    // hang and leak between tests.
    const queryClient = makeQueryClient();
    const seeded = seedRow();
    queryClient.setQueryData<QuarantineRowOut[]>(
      ["quarantine", LEA_ID],
      [seeded],
    );

    server.use(
      http.post(`/api/v1/quarantine/${QUARANTINE_ID}/release`, () =>
        HttpResponse.json({
          quarantine_id: QUARANTINE_ID,
          release_generation_id: "rg-test-001",
          entity_type: "enrollment",
          entity_id: "enr-test-001",
        }),
      ),
      http.get("/api/v1/quarantine", () => HttpResponse.json([])),
    );

    renderWithProviders(
      <QuarantineSection leaId={LEA_ID} onReject={vi.fn()} />,
      { queryClient },
    );

    const releaseButton = await screen.findByRole("button", {
      name: "Release",
    });
    const user = userEvent.setup();
    await user.click(releaseButton);

    await waitFor(() => {
      const current = queryClient.getQueryData<QuarantineRowOut[]>([
        "quarantine",
        LEA_ID,
      ]);
      expect(current).toEqual([]);
    });
  });
});
