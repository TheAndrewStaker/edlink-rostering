/**
 * MSW node server instance for Vitest.
 *
 * Single source of truth so tests can import `server` and call
 * `server.use(...)` to add scenario-specific handlers without
 * redefining the default ones.
 */

import { setupServer } from "msw/node";

import { handlers } from "@/mocks/handlers";

export const server = setupServer(...handlers);
