/**
 * Chakra v3 toaster shared by the whole admin app.
 *
 * One module-level toaster lives here so any component can call
 * notifyError / notifySuccess without threading a hook through props.
 * The matching <AppToaster /> mount in App.tsx renders the queue.
 *
 * The toaster pattern replaces the inline error <Text> we used during
 * Phase 1 (LeaDetailPanel.tsx's quarantine release path). Mutation
 * onError handlers call notifyError so the dialog stays open if the
 * server rejects, and a Chakra toast shows the message at
 * bottom-right.
 */

import { createToaster } from "@chakra-ui/react";

export const toaster = createToaster({
  placement: "bottom-end",
  pauseOnPageIdle: true,
  max: 3,
});

export function notifyError(
  title: string,
  description?: string,
): void {
  toaster.create({
    title,
    description,
    type: "error",
    duration: 6000,
  });
}

export function notifySuccess(
  title: string,
  description?: string,
): void {
  toaster.create({
    title,
    description,
    type: "success",
    duration: 3500,
  });
}

export function notifyFromError(
  defaultTitle: string,
  err: unknown,
): void {
  const message =
    err instanceof Error ? err.message : String(err ?? "Unknown error");
  notifyError(defaultTitle, message);
}
