import { expect, test } from "@playwright/test";
import { installMockApi } from "./fixtures/mock-api";

const ROUTES = [
  ["/", "Overview"], ["/tasks", "Tasks"], ["/facts", "Facts"],
  ["/decisions", "Decisions"], ["/knowledge", "Knowledge"],
  ["/knowledge/doc?id=1", "Retention runbook"], ["/answers", "Approved answers"],
  ["/insights", "Insights"], ["/audit", "Repository audit"], ["/lineage", "Lineage"],
  ["/flows", "Flows"], ["/library", "Library"], ["/publish", "Publish"],
  ["/trajectories", "Agent trajectories"],
  ["/sources", "Sources"], ["/settings/general", "General"],
  ["/settings/models", "Models"], ["/settings/design", "Design & brand"],
  ["/settings/members", "Members"], ["/settings/api-keys", "API keys"],
  ["/settings/audit", "Audit log"], ["/preferences", "Preferences"], ["/welcome", "Welcome"],
] as const;

test.beforeEach(async ({ page }) => { await installMockApi(page); });

for (const [path, title] of ROUTES) {
  test(`${path} renders its browser route`, async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto(path);
    await expect(page.getByText(title, { exact: true }).first()).toBeVisible();
    await expect(path === "/welcome" || path === "/preferences" ? page.locator("body") : page.locator("main")).toBeVisible();
    expect(errors).toEqual([]);
  });
}

test("unknown routes recover to the authenticated overview", async ({ page }) => {
  await page.goto("/does-not-exist");
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByText("Overview", { exact: true }).first()).toBeVisible();
});

test("signed-out protected routes redirect to login", async ({ page }) => {
  await page.unrouteAll({ behavior: "wait" });
  await installMockApi(page, { signedIn: false });
  await page.goto("/facts");
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByText("Sign in", { exact: true }).first()).toBeVisible();
});
