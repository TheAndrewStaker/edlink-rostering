/**
 * Vitest setup file.
 *
 * Boots the MSW server, registers cleanup between tests, and extends
 * Vitest's `expect` with jest-dom matchers. Default handlers live at
 * `src/mocks/handlers.ts`; per-test overrides use `server.use(...)`.
 */

import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll } from "vitest";

import { server } from "@/mocks/server";

beforeAll(() => {
  server.listen({ onUnhandledRequest: "error" });
});

afterEach(() => {
  cleanup();
  server.resetHandlers();
});

afterAll(() => {
  server.close();
});
