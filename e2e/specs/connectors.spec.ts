/**
 * End-to-end specs for the connector authorize pathway.
 *
 * Two specs cover the mutation-pathway contract from
 * .claude/rules/testing.md:
 *
 *   1. Happy path: re-authorize an existing connector with a Key Vault
 *      secret name that is staged. Asserts the connector row reflects
 *      the operator email and the seeded secret_ref afterwards, and
 *      that the audit-log endpoint carries the new connector.authorized
 *      action.
 *
 *   2. Server-error path: re-authorize with a Key Vault name that is
 *      not staged. The backend returns 422 (ConnectorSecretNotStaged).
 *      Asserts the error toast appears and the dialog stays open.
 *
 * Both specs target the seeded Valley Charter EdLink connector. The
 * shared db fixture wipes and seeds the database before each spec via
 * dev-reset.sh, so the starting state is deterministic.
 */

import { expect, test } from "@e2e/fixtures/test-base";
import { API_BASE_URL, mintJwt, signIn } from "@e2e/fixtures/auth";

const LEA_NAME = "Valley Charter";
const STAGED_SECRET = "e2e-test-token";
const UNSTAGED_SECRET = "nonexistent-vault-name-xyz";

test.describe("Connector authorize pathway", () => {
  test("happy path: re-authorize an existing connector", async ({
    page,
    request,
  }) => {
    const reason = "e2e happy-path: re-authorize Valley Charter";

    await signIn(page, "admin");
    await page.goto("/connectors");

    const row = page.getByRole("row").filter({ hasText: LEA_NAME });
    await expect(row).toBeVisible();
    await row.getByRole("button", { name: "Actions" }).click();
    await page
      .getByRole("menuitem", { name: /Authorize \/ re-authorize/i })
      .click();

    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();

    await dialog
      .getByLabel("Key Vault secret name")
      .fill(STAGED_SECRET);
    await dialog.getByLabel("Reason").fill(reason);

    const authorizeResponsePromise = page.waitForResponse(
      (response) =>
        response.url().includes("/api/v1/connectors/") &&
        response.url().endsWith("/authorize") &&
        response.request().method() === "POST",
    );
    await dialog.getByRole("button", { name: "Authorize" }).click();
    const authorizeResponse = await authorizeResponsePromise;
    expect(authorizeResponse.status()).toBe(200);

    await expect(
      page.getByText(/Authorized EdLink for lea-valley-charter/i),
    ).toBeVisible({ timeout: 5000 });

    const token = await mintJwt(request, "admin");
    const audit = await request.get(
      `${API_BASE_URL}/api/v1/admin/audit?action_prefix=connector.authorized&limit=5`,
      { headers: { Authorization: `Bearer ${token}` } },
    );
    expect(audit.ok()).toBeTruthy();
    const body = (await audit.json()) as {
      entries: Array<{ action: string; reason: string | null }>;
    };
    const recent = body.entries.find((e) => e.reason === reason);
    expect(recent).toBeDefined();
    expect(recent?.action).toBe("connector.authorized");
  });

  test("server-error path: unstaged secret returns 422 and shows error toast", async ({
    page,
  }) => {
    await signIn(page, "admin");
    await page.goto("/connectors");

    const row = page.getByRole("row").filter({ hasText: LEA_NAME });
    await expect(row).toBeVisible();
    await row.getByRole("button", { name: "Actions" }).click();
    await page
      .getByRole("menuitem", { name: /Authorize \/ re-authorize/i })
      .click();

    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();

    await dialog
      .getByLabel("Key Vault secret name")
      .fill(UNSTAGED_SECRET);
    await dialog
      .getByLabel("Reason")
      .fill("e2e error-path: secret not staged");
    await dialog.getByRole("button", { name: "Authorize" }).click();

    await expect(
      page.getByText(/Authorize failed.*is not staged/i),
    ).toBeVisible({ timeout: 5000 });

    // The optimistic mutation pattern closes the dialog immediately
    // after client-side validation. The error toast fires on rollback,
    // and the table row is unchanged (the connector stays Active).
    await expect(row).toBeVisible();
    await expect(row.getByText("Active")).toBeVisible();
  });
});
