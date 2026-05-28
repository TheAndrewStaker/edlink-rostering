import { useSyncExternalStore } from "react";

import { getJwt, setUnauthorizedHandler } from "@/api/client";

const listeners = new Set<() => void>();

function subscribe(cb: () => void) {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

function getSnapshot() {
  return getJwt() !== null;
}

export function notifyAuthChange() {
  for (const cb of listeners) cb();
}

// Wire the HTTP client's 401 hook back into the auth store. When any
// API call returns 401, the client clears the JWT; this handler then
// flips `useIsAuthenticated` to false so App.tsx swaps to the
// SignInScreen instead of leaving panels showing inline error strings.
setUnauthorizedHandler(notifyAuthChange);

export function useIsAuthenticated(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot);
}
