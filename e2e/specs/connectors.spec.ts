/**
 * End-to-end spec for the connector authorize pathway.
 *
 * Happy path: re-authorize an existing connector. EdLink owns the
 * district's access token (fetched by the LEA's integration id), so
 * authorize takes only a reason and an optional poll interval; there is
 * no Key Vault secret to stage. Asserts the authorize call returns 200,
 * the success toast fires, and the audit-log endpoint carries the new
 * connector.authorized action.
 *
 * The former server-error spec drove a 422 by staging an unknown Key
 * Vault secret name. That validation was removed with the per-LEA
 * secret model: authorize no longer touches a credential, so the path
 * no longer exists. Authorize-form validation (reason required, Confirm
 * disabled until valid) is covered at the component layer per
 * .claude/rules/testing.md.
 *
 * The spec targets the seeded Valley Charter EdLink connector. The
 * shared db fixture wipes and seeds the database before the spec via
 * dev-reset.sh, so the starting state is deterministic.
 */

import { expect, test } from "@e2e/fixtures/test-base";
import { API_BASE_URL, mintJwt, signIn } from "@e2e/fixtures/auth";

const LEA_NAME = "Valley Charter";

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
    await page.getByRole("menuitem", { name: /Re-authorize/i }).click();

    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();

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
      page.getByText(/Authorized lea-valley-charter/i),
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
});
