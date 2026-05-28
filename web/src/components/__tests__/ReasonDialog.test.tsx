/**
 * Component test for the operator audit-ceremony reason dialog body.
 *
 * Covers the client-side validation contract: Confirm stays disabled
 * until the reason textarea has non-empty trimmed content. The
 * server-side `min_length=1` check in Pydantic is the second line of
 * defense; this test guards the first.
 *
 * The body component is tested in isolation. The full dialog is
 * registered via `createReasonDialog` and rendered through Chakra's
 * Overlay Manager (`Viewport` at app root); end-to-end coverage of
 * the open/close lifecycle lives in the Playwright suite.
 */

import { userEvent } from "@testing-library/user-event";
import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  ReasonDialogBody,
  type ReasonDialogResult,
} from "@/components/ReasonDialog";
import type { ManagedDialogContext } from "@/components/ManagedDialog";
import { renderWithProviders } from "@/__tests__/renderWithProviders";

function makeCtx(): ManagedDialogContext<ReasonDialogResult> & {
  confirmMock: ReturnType<typeof vi.fn>;
  closeMock: ReturnType<typeof vi.fn>;
} {
  const confirmMock = vi.fn();
  const closeMock = vi.fn();
  return {
    confirm: confirmMock,
    close: closeMock,
    confirmMock,
    closeMock,
  };
}

describe("ReasonDialogBody", () => {
  it("disables Confirm until a non-empty trimmed reason is entered", async () => {
    const ctx = makeCtx();
    renderWithProviders(
      <ReasonDialogBody
        config={{
          title: "Release quarantine row",
          description: "Apply the row to canonical.",
          confirmLabel: "Release",
        }}
        ctx={ctx}
      />,
    );

    const confirmButton = await screen.findByRole("button", {
      name: "Release",
    });
    expect(confirmButton).toBeDisabled();

    const textarea = screen.getByRole("textbox");
    const user = userEvent.setup();
    await user.type(textarea, "   ");
    expect(confirmButton).toBeDisabled();

    await user.type(textarea, "operator confirmed with district");
    await waitFor(() => expect(confirmButton).toBeEnabled());

    await user.click(confirmButton);
    expect(ctx.confirmMock).toHaveBeenCalledTimes(1);
    expect(ctx.confirmMock).toHaveBeenCalledWith({
      reason: "operator confirmed with district",
      forced: false,
    });
  });

  it("trims surrounding whitespace from the reason on Confirm", async () => {
    const ctx = makeCtx();
    renderWithProviders(
      <ReasonDialogBody
        config={{
          title: "Reject quarantine row",
          confirmLabel: "Reject",
        }}
        ctx={ctx}
      />,
    );

    const textarea = screen.getByRole("textbox");
    const user = userEvent.setup();
    await user.type(textarea, "   real reason here   ");
    const confirmButton = screen.getByRole("button", { name: "Reject" });
    await waitFor(() => expect(confirmButton).toBeEnabled());
    await user.click(confirmButton);

    expect(ctx.confirmMock).toHaveBeenCalledWith({
      reason: "real reason here",
      forced: false,
    });
  });
});
