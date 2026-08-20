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
    { name: "chromium", use: { ...devices["Desktop Chrome"] }, testIgnore: /\.(live|integration)\.spec\.ts/ },
    { name: "mobile-chromium", use: { ...devices["Pixel 7"] }, testIgnore: /\.(live|integration)\.spec\.ts/ },
    { name: "firefox-smoke", use: { ...devices["Desktop Firefox"] }, testMatch: /navigation\.spec\.ts/ },
    { name: "webkit-smoke", use: { ...devices["Desktop Safari"] }, testMatch: /navigation\.spec\.ts/ },
    ...(process.env.MARI_E2E_LIVE === "1"
      ? [{
          name: "live-chromium",
          // Live forms contain real connector credentials. Never persist a
          // trace, video, or failure screenshot from this project.
          use: { ...devices["Desktop Chrome"], trace: "off", video: "off", screenshot: "off" },
          testMatch: /\.live\.spec\.ts/,
          workers: 1,
        }]
      : []),
    ...(process.env.MARI_E2E_INTEGRATION === "1"
      ? [{
          name: "integration-chromium",
          use: { ...devices["Desktop Chrome"], trace: "retain-on-failure" as const },
          testMatch: /\.integration\.spec\.ts/,
          workers: 1,
        }]
      : []),
  ],
});
