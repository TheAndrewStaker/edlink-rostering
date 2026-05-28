/**
 * Shared Playwright test fixture with auto DB reset.
 *
 * Every spec inherits a clean dev seed by importing `test` from this
 * module instead of `@playwright/test` directly. The `db` fixture
 * runs `scripts/dev-reset.sh` before the spec, so the
 * suite is deterministic regardless of what the previous spec did.
 *
 * Per-spec reset is the explicit policy until the first three specs
 * land; revisit after suite runtime exceeds 60 seconds. See
 * .claude/rules/testing.md § "DB state in e2e".
 */

import { execSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { test as base } from "@playwright/test";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const PROTOTYPE_DIR = path.resolve(__dirname, "..", "..");

interface EdlinkFixtures {
  db: void;
}

export const test = base.extend<EdlinkFixtures>({
  db: [
    async ({}, use) => {
      execSync("bash scripts/dev-reset.sh", {
        cwd: PROTOTYPE_DIR,
        stdio: "pipe",
      });
      await use();
    },
    { auto: true, timeout: 60_000 },
  ],
});

export { expect } from "@playwright/test";
