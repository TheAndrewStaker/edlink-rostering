/**
 * Renders the shared Chakra v3 Toaster queue.
 *
 * Mounted once in App.tsx so every component can call
 * notifyError / notifySuccess from `@/lib/notify`. The toast appears
 * bottom-right and dismisses after the duration set in the call.
 */

import { Portal, Stack, Toast, Toaster } from "@chakra-ui/react";

import { toaster } from "@/lib/notify";

export function AppToaster() {
  return (
    <Portal>
      <Toaster toaster={toaster} insetInline={{ mdDown: "4" }}>
        {(toast) => (
          <Toast.Root width={{ md: "sm" }}>
            <Toast.Indicator />
            <Stack gap={1} flex={1} maxW="100%">
              {toast.title && <Toast.Title>{toast.title}</Toast.Title>}
              {toast.description && (
                <Toast.Description>{toast.description}</Toast.Description>
              )}
            </Stack>
            <Toast.CloseTrigger />
          </Toast.Root>
        )}
      </Toaster>
    </Portal>
  );
}
