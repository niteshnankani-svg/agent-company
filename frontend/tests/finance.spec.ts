import { test, expect } from "@playwright/test";

// Hits the real, running Agent Company backend (no mocking) — set
// NEXT_PUBLIC_API_BASE_URL in .env.local to the live backend before running.
test("Finance (AR) request renders a real final decision", async ({ page }) => {
  await page.goto("/");

  await page.getByTestId("view-toggle-advanced").click();
  await page.getByTestId("scenario-tab-finance_ar").click();
  await page.getByTestId("run-request-button").click();

  const decision = page.getByTestId("result-decision");
  await expect(decision).toBeVisible({ timeout: 45_000 });
  await expect(decision).not.toHaveText("");

  await expect(page.getByTestId("result-panel")).toContainText("%");
});
