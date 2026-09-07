import { test, expect } from "@playwright/test";

// Hits the real, running Agent Company backend (no mocking) — set
// NEXT_PUBLIC_API_BASE_URL in .env.local to the live backend before running.
test("Ask console is the default view with no department pre-selected", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("view-toggle-ask")).toBeVisible();
  await expect(page.getByTestId("ask-input")).toBeVisible();
  await expect(page.getByTestId("finance-followup-form")).not.toBeVisible();
});

test("Free text routed to Finance without numeric fields triggers the follow-up form, and resubmitting resolves it", async ({ page }) => {
  // Two sequential real API round-trips (initial 500 + follow-up resubmit) —
  // the default 60s test timeout is too tight for both 45s waits back to back.
  test.setTimeout(120_000);
  await page.goto("/");

  // finance.py:170 raises a plain ValueError when sub_department isn't
  // "ar"/"ap" — api.py turns that into an HTTP 500 with no audit_trail.
  // isFinanceNeedsFieldsError() (lib/api.ts) detects that specific case from
  // the error text and the UI should show the follow-up form instead of a
  // generic error banner.
  //
  // finance_head_node checks finance_cache.get(raw_input) BEFORE validating
  // sub_department (finance.py:158) — a fuzzy text-similarity cache
  // (difflib.SequenceMatcher, threshold 0.92), not an exact-match one. Any
  // raw_input close enough to a previously-*successful* Finance submission
  // (e.g. from a prior test run, or manual testing) short-circuits straight
  // to a cached result and never reaches the sub_department check at all —
  // so a fixed phrase here would make this test order-/history-dependent.
  // Randomizing it each run guarantees a cache miss and a real crash.
  const uniqueRef = Math.random().toString(36).slice(2, 10);
  await page.getByTestId("ask-input").fill(`Loan approval and credit assessment request — ref ${uniqueRef}`);
  await page.getByTestId("ask-submit").click();

  const followUp = page.getByTestId("finance-followup-form");
  await expect(followUp).toBeVisible({ timeout: 45_000 });
  await expect(followUp).toContainText("Finance");

  await followUp.getByRole("button", { name: /submit to finance/i }).click();

  const decision = page.getByTestId("result-decision");
  await expect(decision).toBeVisible({ timeout: 45_000 });
  await expect(decision).not.toHaveText("");
  await expect(page.getByTestId("finance-followup-form")).not.toBeVisible();
});

test("Free text routed to Export/Legal does not crash (fields are mirrored from raw_input)", async ({ page }) => {
  await page.goto("/");

  await page.getByTestId("ask-input").fill("What is the HS code for cotton t-shirts?");
  await page.getByTestId("ask-submit").click();

  const decision = page.getByTestId("result-decision");
  await expect(decision).toBeVisible({ timeout: 45_000 });
  await expect(page.getByTestId("request-error")).not.toBeVisible();
});
