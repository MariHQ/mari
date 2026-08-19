import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  outputDir: "../.artifacts/playwright",
  snapshotDir: "./e2e/snapshots",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 2 : undefined,
  reporter: process.env.CI ? [["line"], ["html", { outputFolder: "../.artifacts/playwright-report", open: "never" }]] : "line",
  use: {
    baseURL: process.env.MARI_E2E_BASE_URL || "http://127.0.0.1:4173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  expect: { timeout: 8_000 },
  timeout: 30_000,
  webServer: process.env.MARI_E2E_EXTERNAL_SERVER ? undefined : {
    command: "npm run dev -- --host 127.0.0.1 --port 4173",
    url: "http://127.0.0.1:4173/login",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] }, testIgnore: /\.live\.spec\.ts/ },
    { name: "mobile-chromium", use: { ...devices["Pixel 7"] }, testMatch: /navigation\.spec\.ts/ },
    ...(process.env.MARI_E2E_LIVE === "1"
      ? [{
          name: "live-chromium",
          use: { ...devices["Desktop Chrome"], trace: "off", video: "off" },
          testMatch: /\.live\.spec\.ts/,
          workers: 1,
        }]
      : []),
  ],
});
