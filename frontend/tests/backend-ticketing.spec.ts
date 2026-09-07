import { test, expect } from "@playwright/test";

// Hits the real, running Agent Company backend (no mocking) — set
// NEXT_PUBLIC_API_BASE_URL in .env.local to the live backend before running.
// backend_head_node uses the local DistilBERT classifier and returns a review
// handoff. It needs only raw_input; department_fields is unused.
test("Backend/Ticketing (Advanced tab) renders the backend review handoff", async ({ page }) => {
  await page.goto("/");

  await page.getByTestId("view-toggle-advanced").click();
  await page.getByTestId("scenario-tab-backend_ticketing").click();
  await page.getByTestId("run-request-button").click();

  const decision = page.getByTestId("result-decision");
  await expect(decision).toBeVisible({ timeout: 45_000 });
  await expect(decision).not.toHaveText("");

  await expect(page.getByText("Backend Head", { exact: false })).toBeVisible();
});
