/**
 * Routing specs for /leas (Session 18 split).
 *
 * Covers the URL contract a support URL relies on: drawer state,
 * filter axes, and back-button behavior. The four scenarios mirror
 * the e2e checklist in poc-session-18-plan.md.
 *
 * Each spec asserts the public contract on the URL and on the
 * rendered table or drawer; selectors prefer roles + accessible
 * names per .claude/rules/testing.md.
 */

import { expect, test } from "@e2e/fixtures/test-base";
import { signIn } from "@e2e/fixtures/auth";

const LEA_ID = "lea-lakewood-usd";
const LEA_NAME = "Lakewood Unified School District";

test.describe("/leas routing", () => {
  test("?lea=<id> opens the drawer pre-selected", async ({ page }) => {
    await signIn(page, "owner");
    await page.goto(`/leas?lea=${LEA_ID}`);

    const drawer = page.getByRole("dialog");
    await expect(drawer).toBeVisible();
    await expect(
      drawer.getByRole("heading", { name: LEA_NAME }),
    ).toBeVisible();
  });

  test("closing the drawer strips ?lea= and keeps other params", async ({
    page,
  }) => {
    await signIn(page, "owner");
    await page.goto(`/leas?q=lakewood&lea=${LEA_ID}`);

    const drawer = page.getByRole("dialog");
    await expect(drawer).toBeVisible();
    await drawer.getByRole("button", { name: "Close" }).click();
    await expect(drawer).toBeHidden();

    await expect(page).toHaveURL(/\/leas\?q=lakewood$/);
  });

  test("back button after closing the drawer returns to the prior route", async ({
    page,
  }) => {
    await signIn(page, "owner");
    await page.goto(`/leas?lea=${LEA_ID}`);

    const drawer = page.getByRole("dialog");
    await expect(drawer).toBeVisible();
    await drawer.getByRole("button", { name: "Close" }).click();
    await expect(drawer).toBeHidden();
    await expect(page).toHaveURL(/\/leas$/);

    await page.goBack();
    await expect(page).toHaveURL(/\/$/);
  });

  test("filter params round-trip between URL and inputs", async ({ page }) => {
    await signIn(page, "owner");
    // q=valley matches the seeded Valley Charter LEA, which classifies
    // as critical (latest sync failed). With severity=critical applied,
    // that row stays visible and the column headers render so the
    // sort indicator is reachable.
    await page.goto(
      "/leas?q=valley&severity=critical&sort=name&dir=asc",
    );

    const search = page.getByLabel("Search LEAs");
    await expect(search).toHaveValue("valley");

    const criticalChip = page.getByRole("button", { name: "Critical" });
    await expect(criticalChip).toHaveAttribute("aria-pressed", "true");
    const warningChip = page.getByRole("button", { name: "Warning" });
    await expect(warningChip).toHaveAttribute("aria-pressed", "false");

    await expect(
      page.getByRole("option", { name: /Name ↑/, selected: true }),
    ).toBeAttached();

    await warningChip.click();
    await expect(page).toHaveURL(
      /severity=critical.*severity=warning|severity=warning.*severity=critical/,
    );
  });
});
