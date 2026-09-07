import { test, expect } from "@playwright/test";

// Hits the real, running Agent Company backend (no mocking) — set
// NEXT_PUBLIC_API_BASE_URL in .env.local to the live backend before running.
test("Export/Compliance request resolves an HS code and renders it", async ({ page }) => {
  await page.goto("/");

  await page.getByTestId("view-toggle-advanced").click();
  await page.getByTestId("scenario-tab-export_compliance").click();
  await page.getByTestId("run-request-button").click();

  const decision = page.getByTestId("result-decision");
  await expect(decision).toBeVisible({ timeout: 45_000 });
  await expect(decision).not.toHaveText("");

  // The graph should have actually traversed the orchestrator + export head.
  await expect(page.getByText("Orchestrator", { exact: false })).toBeVisible();
  await expect(page.getByText("Export Head", { exact: false })).toBeVisible();
});
