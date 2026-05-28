/**
 * Playwright configuration for the admin e2e suite.
 *
 * Playwright owns the API and Vite lifecycles via `webServer`. Both
 * use the shell scripts that the dev loop already uses, so spec runs
 * and dev runs go through the same code paths.
 *
 * Per .claude/rules/testing.md:
 *   - chromium only for the POC
 *   - workers serial on CI (DB isolation policy revisits after first
 *     three specs land); local parallelism left to Playwright's default
 *   - trace on first retry, video + screenshot on failure for postmortem
 */

import { defineConfig, devices } from "@playwright/test";

// Ports come from the parent shell via scripts/_lib.sh
// (EDLINK_PORT_BASE drives PORT_API and PORT_WEB = PORT_API + 1).
// scripts/e2e.sh sources _lib.sh before invoking playwright so the
// env vars are populated. Defaults match .env.example.
const API_PORT = Number(process.env.PORT_API ?? 8100);
const WEB_PORT = Number(process.env.PORT_WEB ?? API_PORT + 1);

// Headed-mode tuning. When PLAYWRIGHT_HEADED=1 (set by e2e-headed.sh),
// drop the slow-mo delay so each action takes a visible beat in the
// browser and the operator can actually watch the dialog open, fill,
// submit, and the toast fire. The default headless run keeps slowMo
// at 0 so CI stays fast.
const HEADED = process.env.PLAYWRIGHT_HEADED === "1";
const SLOW_MO_MS = HEADED
  ? Number.parseInt(process.env.PLAYWRIGHT_SLOWMO_MS ?? "400", 10)
  : 0;

export default defineConfig({
  testDir: "./specs",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: process.env.CI ? [["github"], ["html"]] : "list",

  use: {
    baseURL: process.env.WEB_URL ?? `http://localhost:${WEB_PORT}`,
    trace: HEADED ? "on" : "on-first-retry",
    video: HEADED ? "on" : "retain-on-failure",
    screenshot: "only-on-failure",
    actionTimeout: 10_000,
    navigationTimeout: 15_000,
    headless: !HEADED,
    launchOptions: {
      slowMo: SLOW_MO_MS,
    },
  },

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],

  webServer: [
    {
      command: "bash scripts/api-serve.sh",
      cwd: "..",
      url: `http://127.0.0.1:${API_PORT}/api/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
      stdout: "ignore",
      stderr: "pipe",
    },
    {
      command: "bash scripts/web-dev.sh",
      cwd: "..",
      url: `http://localhost:${WEB_PORT}`,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
      stdout: "ignore",
      stderr: "pipe",
    },
  ],
});
