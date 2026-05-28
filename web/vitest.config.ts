/**
 * Vitest configuration for the EdLink rostering admin app component tests.
 *
 * Shares Vite plugins and the `@/` path alias with the dev config so
 * `vite.config.ts` and this file do not drift. Component tests live
 * alongside the components they cover at `src/**\/__tests__\/*.test.tsx`.
 */

import path from "node:path";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  test: {
    environment: "jsdom",
    globals: false,
    setupFiles: ["./src/setupTests.ts"],
    include: ["src/**/__tests__/**/*.test.{ts,tsx}"],
    css: true,
  },
});
