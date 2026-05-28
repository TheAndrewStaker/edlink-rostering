/**
 * Default MSW request handlers for component tests.
 *
 * Keep this shell minimal. Per-test overrides via `server.use(...)`
 * are the right place for mutation-specific responses; this file is
 * for shared GET endpoints that a lot of tests need to render
 * components without errors.
 */

import { HttpResponse, http } from "msw";

export const handlers = [
  http.get("/api/health", () => HttpResponse.json({ status: "ok" })),
];
