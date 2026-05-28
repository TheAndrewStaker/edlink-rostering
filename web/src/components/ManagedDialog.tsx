/**
 * ManagedDialog: Chakra v3 Overlay-Manager wrapper for the admin app.
 *
 * USE THIS for every dialog. Do not render Chakra's `Dialog.Root`
 * directly. See the original file header for the full rationale on
 * overlay stack corruption.
 */

import {
  CloseButton,
  Dialog,
  Portal,
  createOverlay,
} from "@chakra-ui/react";
import { type ReactNode } from "react";

export interface ManagedDialogContext<R = void> {
  close: () => void;
  confirm: (result: R) => void;
}

export interface ManagedDialogConfig<T, R = void> {
  size?: "xs" | "sm" | "md" | "lg" | "xl";
  placement?: "center" | "top";
  title: (value: T) => ReactNode;
  subtitle?: (value: T) => ReactNode;
  body: (value: T, ctx: ManagedDialogContext<R>) => ReactNode;
  footer?: (value: T, ctx: ManagedDialogContext<R>) => ReactNode;
}

export interface ManagedDialogHandle<T, R = void> {
  open: (value: T) => Promise<R | undefined>;
  close: (result?: R) => void;
  Viewport: React.ComponentType;
}

interface InternalProps<T> {
  value: T;
}

const OVERLAY_ID = "instance";

export function createManagedDialog<T, R = void>(
  config: ManagedDialogConfig<T, R>,
): ManagedDialogHandle<T, R> {
  const overlay = createOverlay<InternalProps<T>>((props) => {
    const {
      open,
      onOpenChange,
      onExitComplete,
      setReturnValue,
      value,
    } = props as InternalProps<T> & {
      open?: boolean;
      onOpenChange?: (e: { open: boolean }) => void;
      onExitComplete?: () => void;
      setReturnValue?: (v: unknown) => void;
    };

    const handleClose = () => {
      onOpenChange?.({ open: false });
    };
    const handleConfirm = (result: R) => {
      setReturnValue?.(result);
      onOpenChange?.({ open: false });
    };
    const ctx: ManagedDialogContext<R> = {
      close: handleClose,
      confirm: handleConfirm,
    };

    return (
      <Dialog.Root
        open={open}
        onOpenChange={onOpenChange}
        onExitComplete={onExitComplete}
        size={config.size ?? "md"}
        placement={config.placement ?? "center"}
        unmountOnExit
        lazyMount
      >
        <Portal>
          <Dialog.Backdrop
            style={{ background: "rgba(21, 23, 26, 0.55)" }}
          />
          <Dialog.Positioner>
            <Dialog.Content
              style={{
                background: "var(--panel)",
                borderRadius: 12,
                border: "1px solid var(--rule-strong)",
                boxShadow: "0 24px 48px rgba(0,0,0,0.18)",
                maxHeight: "90vh",
                overflow: "hidden",
                fontFamily: "var(--font-body)",
              }}
            >
              <Dialog.Header
                style={{
                  padding: "18px 22px 14px",
                  borderBottom: "1px solid var(--rule)",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    alignItems: "flex-start",
                    justifyContent: "space-between",
                    width: "100%",
                    gap: 12,
                  }}
                >
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="eyebrow" style={{ marginBottom: 4 }}>
                      Operator action
                    </div>
                    <h3
                      style={{
                        margin: "4px 0 0 0",
                        fontSize: 18,
                        fontWeight: 600,
                      }}
                    >
                      {config.title(value)}
                    </h3>
                    {config.subtitle &&
                      (() => {
                        const node = config.subtitle(value);
                        if (node == null || node === "") return null;
                        return (
                          <div
                            style={{
                              fontSize: 12.5,
                              color: "var(--ink-2)",
                              marginTop: 4,
                            }}
                          >
                            {node}
                          </div>
                        );
                      })()}
                  </div>
                  <CloseButton
                    size="sm"
                    onClick={handleClose}
                    style={{ flexShrink: 0 }}
                  />
                </div>
              </Dialog.Header>
              <Dialog.Body
                style={{
                  padding: "18px 22px",
                  overflowY: "auto",
                }}
              >
                {config.body(value, ctx)}
              </Dialog.Body>
              {config.footer && (
                <Dialog.Footer
                  style={{
                    display: "flex",
                    gap: 8,
                    justifyContent: "flex-end",
                    padding: "14px 22px",
                    borderTop: "1px solid var(--rule)",
                    background: "var(--panel-2)",
                  }}
                >
                  {config.footer(value, ctx)}
                </Dialog.Footer>
              )}
            </Dialog.Content>
          </Dialog.Positioner>
        </Portal>
      </Dialog.Root>
    );
  });

  const open = (value: T): Promise<R | undefined> =>
    overlay.open(OVERLAY_ID, { value } as InternalProps<T>) as Promise<
      R | undefined
    >;

  const close = (result?: R) => {
    void overlay.close(OVERLAY_ID, result);
  };

  return {
    open,
    close,
    Viewport: overlay.Viewport as React.ComponentType,
  };
}
