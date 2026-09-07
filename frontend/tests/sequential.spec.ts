import { test, expect } from "@playwright/test";

// Hits the real, running Agent Company backend (no mocking) — set
// NEXT_PUBLIC_API_BASE_URL in .env.local to the live backend before running.
test("Sequential Export -> Finance renders both department cards with history", async ({ page }) => {
  await page.goto("/");

  await page.getByTestId("view-toggle-advanced").click();
  await page.getByTestId("scenario-tab-sequential_export_finance").click();
  await page.getByTestId("run-request-button").click();

  const history = page.getByTestId("department-history");
  await expect(history).toBeVisible({ timeout: 60_000 });

  await expect(page.getByTestId("department-card-export_compliance")).toBeVisible();
  await expect(page.getByTestId("department-card-finance")).toBeVisible();

  const decision = page.getByTestId("result-decision");
  await expect(decision).toBeVisible();
  await expect(decision).not.toHaveText("");

  // Final result is explicitly attributed to whichever department ran last.
  await expect(page.getByTestId("result-panel")).toContainText("final result");
});
