import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

// The frontend must contain no classification or routing logic of its own.
function walk(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = path.join(dir, e.name);
    return e.isDirectory() ? walk(p) : [p];
  });
}

test("no router, classifier SDK or department table exists in the frontend source", () => {
  const root = path.resolve(__dirname, "..");
  const files = ["app", "lib", "components"].flatMap((d) => walk(path.join(root, d))).filter((f) => /\.(ts|tsx|json)$/.test(f));
  const forbidden = /typesafe|DEPARTMENT_CRITERIA|INJECTION_RE|SECURITY_RE|localClassify|predictHelpdesk/i;
  const offenders = files.filter((f) => forbidden.test(fs.readFileSync(f, "utf8")));
  expect(offenders).toEqual([]);
  expect(fs.existsSync(path.join(root, "lib", "orchestrator.ts"))).toBe(false);
  expect(fs.readFileSync(path.join(root, "package.json"), "utf8")).not.toMatch(/typesafe/i);
});
