import { test, expect, type Page } from "@playwright/test";

const MOCK = "http://127.0.0.1:8099";

async function ask(page: Page, text: string) {
  await page.goto("/");
  await page.getByTestId("ask-input").fill(text);
  await page.getByTestId("ask-submit").click();
}

test("the page is one free-text box; there is no way to pick a department", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("textbox")).toHaveCount(1);
  await expect(page.locator("select, input[type=radio], [role=combobox], [role=tab], [role=listbox]")).toHaveCount(0);
  for (const dept of [/finance/i, /legal/i, /export/i, /backend/i, /\bhr\b/i]) {
    await expect(page.getByRole("button", { name: dept })).toHaveCount(0);
  }
});

test("health indicator reports the API as online", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("health-indicator")).toContainText("online");
});

test("answered: shows the answer, the evidence relied on, and a labelled retrieval score", async ({ page }) => {
  await ask(page, "answered-legal What is Section 357 of BNS about?");
  await expect(page.getByTestId("result-status")).toHaveAttribute("data-status", "answered");
  await expect(page.getByTestId("result-answer")).toContainText("breach of contract");
  await expect(page.getByText("legal_rag:BNS Section 357")).toBeVisible();
  await expect(page.getByTestId("result-confidence")).toContainText("0.64");
  await expect(page.getByTestId("result-confidence")).toContainText("not the chance the answer is right");
});

test("answered with no real confidence number shows none", async ({ page }) => {
  await ask(page, "answered-hr how many in sales");
  await expect(page.getByTestId("result-status")).toHaveAttribute("data-status", "answered");
  await expect(page.getByTestId("result-confidence")).toHaveCount(0);
});

test("needs_human: says a person must decide and why, in plain words", async ({ page }) => {
  await ask(page, "human review please");
  await expect(page.getByTestId("result-status")).toHaveAttribute("data-status", "needs_human");
  await expect(page.getByTestId("result-needs-human")).toContainText("passed to a person");
  await expect(page.getByTestId("result-needs-human")).toContainText("Security Team");
});

test("needs_more_info: lists the missing fields", async ({ page }) => {
  await ask(page, "more-info credit check");
  await expect(page.getByTestId("result-status")).toHaveAttribute("data-status", "needs_more_info");
  await expect(page.getByTestId("result-needs-more-info")).toContainText("credit score");
  await expect(page.getByTestId("result-needs-more-info")).toContainText("loan amount");
});

test("failed: says which service failed", async ({ page }) => {
  await ask(page, "failed lookup");
  await expect(page.getByTestId("result-status")).toHaveAttribute("data-status", "failed");
  await expect(page.getByTestId("result-failed")).toContainText("legal_rag: request_failed");
});

test("the real audit trail is replayed", async ({ page }) => {
  await ask(page, "answered-legal question");
  await expect(page.getByText("Legal Head")).toBeVisible();
  await expect(page.getByText("Verifier")).toBeVisible();
});

test("the console forwards only the sentence and an id; the API decides everything else", async ({ page, request }) => {
  await ask(page, "answered-legal a sentence");
  await expect(page.getByTestId("result-status")).toBeVisible();
  const last = await (await request.get(`${MOCK}/__last`)).json();
  expect(Object.keys(last).sort()).toEqual(["raw_input", "request_id"]);
  expect(last.raw_input).toBe("answered-legal a sentence");
});

test("an upstream error is reported without leaking its text, and no substitute answer appears", async ({ page }) => {
  await ask(page, "upstream-down please");
  const err = page.getByTestId("request-error");
  await expect(err).toBeVisible();
  await expect(err).toContainText("could not process this request");
  await expect(err).not.toContainText("SECRET_UPSTREAM_TRACEBACK");
  await expect(page.getByTestId("result-panel")).toHaveCount(0);
});

test("the route rejects empty, oversized and malformed input before calling the API", async ({ request }) => {
  const empty = await request.post("/api/agent/request", { data: { request_id: "x", raw_input: "   " } });
  expect(empty.status()).toBe(400);
  const huge = await request.post("/api/agent/request", { data: { request_id: "x", raw_input: "a".repeat(8001) } });
  expect(huge.status()).toBe(400);
  const notJson = await request.post("/api/agent/request", { headers: { "Content-Type": "application/json" }, data: "{nope" });
  expect(notJson.status()).toBe(400);
});
