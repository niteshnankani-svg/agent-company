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

test("a verified answer says it was checked against the evidence", async ({ page }) => {
  await ask(page, "answered-legal What is Section 357 of BNS about?");
  const v = page.getByTestId("result-verification");
  await expect(v).toHaveAttribute("data-tone", "good");
  await expect(v).toContainText("every statement is supported by the evidence");
});

test("an unchecked answer is never presented as checked", async ({ page }) => {
  await ask(page, "unchecked-backend laptop");
  await expect(page.getByTestId("result-status")).toHaveAttribute("data-status", "answered");
  const v = page.getByTestId("result-verification");
  await expect(v).toHaveAttribute("data-tone", "warn");
  await expect(v).toContainText("Not independently checked");
  await expect(v).toContainText("does not provide its sources");
  await expect(v).not.toContainText("Checked:");
});

test("an answer withdrawn by the verifier is not shown as an answer; the draft and findings go to the reviewer", async ({ page }) => {
  await ask(page, "withdrawn what is the fine");
  await expect(page.getByTestId("result-status")).toHaveAttribute("data-status", "needs_human");
  await expect(page.getByTestId("result-answer")).toHaveCount(0);
  await expect(page.getByTestId("result-verification")).toHaveCount(0);
  await expect(page.getByTestId("result-needs-human")).toContainText("some statements were not supported");
  await expect(page.getByText("draft for the reviewer (not verified)")).toBeVisible();
  await expect(page.getByText("verifier", { exact: true })).toBeVisible();
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

test("a request passed to a person shows a reference, and the user can check whether they have decided", async ({ page, request }) => {
  await ask(page, "human review please");
  const ref = page.getByTestId("run-reference");
  await expect(ref).toHaveText(/^[0-9a-f]{32}$/);
  await page.getByTestId("check-decision").click();
  await expect(page.getByTestId("still-waiting")).toBeVisible();
  await expect(page.getByTestId("result-status")).toHaveAttribute("data-status", "needs_human");

  await request.get(`${MOCK}/__resolve/${await ref.innerText()}`); // a reviewer decides
  await page.getByTestId("check-decision").click();
  await expect(page.getByTestId("result-status")).toHaveAttribute("data-status", "answered");
  await expect(page.getByTestId("result-answer")).toContainText("A person reviewed this");
  await expect(page.getByTestId("result-verification")).toContainText("Answered by asha");
  await expect(page.getByTestId("pending-review")).toHaveCount(0);
});

test("a request a person declined is shown as declined, not as a service failure", async ({ page }) => {
  await ask(page, "declined something");
  await expect(page.getByTestId("result-declined")).toContainText("declined it");
  await expect(page.getByTestId("result-declined")).toContainText("out of scope");
  await expect(page.getByTestId("result-failed")).toHaveCount(0);
});

test("the console authenticates to the API with its own key, and the key never reaches the browser", async ({ page, request }) => {
  const seenByBrowser: string[] = [];
  page.on("response", async (res) => {
    try { seenByBrowser.push(await res.text()); } catch { /* binary or redirected */ }
  });
  await ask(page, "answered-legal What is Section 357 of BNS about?");
  await expect(page.getByTestId("result-status")).toHaveAttribute("data-status", "answered"); // the mock 401s without the key
  const { lastAuth } = await (await request.get(`${MOCK}/__auth`)).json();
  expect(lastAuth).toBe("Bearer test-console-key-0123456789abcdef");
  expect(seenByBrowser.join("\n")).not.toContain("test-console-key");
  expect(await page.content()).not.toContain("test-console-key");
});

test("the console exposes only a read-only status lookup, never the reviewer actions", async ({ request }) => {
  expect((await request.get("/api/agent/runs/not-a-reference")).status()).toBe(400);
  expect((await request.get(`/api/agent/runs/${"0".repeat(32)}`)).status()).toBe(404);
  expect((await request.post(`/api/agent/runs/${"0".repeat(32)}`, { data: {} })).status()).toBe(405);
  expect((await request.get("/api/agent/reviews")).status()).toBe(404);
  expect((await request.post("/api/agent/reviews/x/resume", { data: {} })).status()).toBe(404);
});

test("needs_more_info without a form spec: lists the missing fields", async ({ page }) => {
  await ask(page, "more-list credit check");
  await expect(page.getByTestId("result-status")).toHaveAttribute("data-status", "needs_more_info");
  await expect(page.getByTestId("result-needs-more-info")).toContainText("credit score");
  await expect(page.getByTestId("result-needs-more-info")).toContainText("loan amount");
});

test("failed: says which service failed", async ({ page }) => {
  await ask(page, "failed lookup");
  await expect(page.getByTestId("result-status")).toHaveAttribute("data-status", "failed");
  await expect(page.getByTestId("result-failed")).toContainText("legal_rag: request_failed");
});

test("needs_more_info with a form: renders exactly the questions the backend asked for", async ({ page }) => {
  await ask(page, "form pay invoice INV-4021 for 45000");
  await expect(page.getByTestId("result-status")).toHaveAttribute("data-status", "needs_more_info");
  const form = page.getByTestId("follow-up-form");
  await expect(form).toBeVisible();
  await expect(form.getByText("Is this the first time we are paying this vendor?")).toBeVisible();
  await expect(form.getByText("Leave blank for a first-time vendor.")).toBeVisible();
  await expect(form.getByRole("radio")).toHaveCount(2); // yes / no for the one boolean
  await expect(form.getByRole("spinbutton")).toHaveCount(2); // the two numeric fields
  // nothing the backend did not ask for
  await expect(form.getByText(/credit score|gst|overdue/i)).toHaveCount(0);
});

test("submitting the form sends the original sentence, what was understood, and the answers", async ({ page, request }) => {
  await ask(page, "form pay invoice INV-4021 for 45000");
  await page.getByTestId("ask-input").fill("something else typed afterwards"); // must not leak into the follow-up
  const form = page.getByTestId("follow-up-form");
  await form.getByLabel("No").check();
  await form.getByLabel("Payment terms (days)").fill("30");
  await form.getByTestId("follow-up-submit").click();

  await expect(page.getByTestId("result-needs-human")).toContainText("approved automatically");
  await expect(page.getByTestId("follow-up-form")).toHaveCount(0);
  const last = await (await request.get(`${MOCK}/__last`)).json();
  expect(last.raw_input).toBe("form pay invoice INV-4021 for 45000");
  expect(last.follow_up).toEqual({
    department: "finance",
    sub_department: "ap",
    // numbers are numbers, the boolean is a boolean, blank fields are left out, collected values come back
    fields: { invoice_amount: 45000, vendor_is_first_time: false, payment_terms_days: 30 },
  });
});

test("a rejected answer reappears on the form with the reason, next to the field", async ({ page }) => {
  await ask(page, "form pay invoice");
  const form = page.getByTestId("follow-up-form");
  await form.getByLabel("Payment terms (days)").fill("99999");
  await form.getByTestId("follow-up-submit").click();
  const field = page.getByTestId("field-payment_terms_days");
  await expect(field.getByRole("alert")).toContainText("less than or equal to 3650");
});

test("when the message could not be read automatically the form says so", async ({ page }) => {
  await ask(page, "form-unavailable pay invoice");
  await expect(page.getByTestId("follow-up-form")).toContainText("could not be read automatically");
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
  const bad = (follow_up: unknown) => request.post("/api/agent/request", { data: { request_id: "x", raw_input: "hi", follow_up } });
  expect((await bad({ fields: {} })).status()).toBe(400); // no department
  expect((await bad({ department: "finance", fields: [] })).status()).toBe(400); // fields must be an object
  expect((await bad({ department: "finance", fields: { a: { nested: 1 } } })).status()).toBe(400); // values must be simple
  expect((await bad({ department: "finance", fields: { a: "x".repeat(201) } })).status()).toBe(400);
  const notJson = await request.post("/api/agent/request", { headers: { "Content-Type": "application/json" }, data: "{nope" });
  expect(notJson.status()).toBe(400);
});
