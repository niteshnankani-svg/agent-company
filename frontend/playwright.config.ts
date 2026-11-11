import { defineConfig, devices } from "@playwright/test";

const PORT = process.env.PORT ? Number(process.env.PORT) : 3000;
const MOCK_PORT = 8099;

export default defineConfig({
  testDir: "./tests",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  reporter: [["list"]],
  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        // Optional: use an already-installed Chromium instead of the one Playwright downloads.
        launchOptions: { executablePath: process.env.PW_CHROMIUM_PATH || undefined },
      },
    },
  ],
  webServer: [
    {
      // A mock of the Python API, so these tests need no backend and no keys.
      command: "node tests/mock-api.mjs",
      url: `http://127.0.0.1:${MOCK_PORT}/health`,
      reuseExistingServer: !process.env.CI,
    },
    {
      command: "npm run dev",
      url: `http://localhost:${PORT}`,
      reuseExistingServer: !process.env.CI,
      timeout: 90_000,
      env: { AGENT_COMPANY_API_URL: `http://127.0.0.1:${MOCK_PORT}` },
    },
  ],
});
