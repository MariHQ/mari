import { expect, test, type Page } from "@playwright/test";

async function signIn(page: Page) {
  await page.goto("/login");
  const bypass = page.getByRole("button", { name: /Continue as workspace admin/ });
  await expect(bypass).toBeVisible();
  await bypass.click();
  await expect(page).toHaveURL(/\/$/);
}

test.beforeEach(async ({ page }) => { await signIn(page); });

test("assembled stack serves health and hardened web responses", async ({ page }) => {
  const health = await page.request.get("/readyz");
  expect(health.ok()).toBeTruthy();
  const body = await health.json();
  expect(body.ok).toBe(true);
  expect(body.dependencies.database).toBe("ok");

  const response = await page.request.get("/");
  expect(response.headers()["x-content-type-options"]).toBe("nosniff");
  expect(response.headers()["x-frame-options"]).toBe("DENY");
  expect(response.headers()["content-security-policy"]).toContain("default-src 'self'");
  expect((await page.request.get("/metrics")).status()).toBe(404);
});

test("real Postgres knowledge is searchable through the production web image", async ({ page }) => {
  await page.goto("/knowledge?q=retention");
  await expect(page.getByText("Retention runbook", { exact: true }).first()).toBeVisible();
  await expect(page.getByText(/retained for 30 days/i).first()).toBeVisible();
});

test("review writes persist through GraphQL and a browser reload", async ({ page }) => {
  await page.goto("/tasks");
  const title = `CI persisted review ${Date.now()}`;
  await page.getByLabel("Review item").fill(title);
  await page.getByRole("button", { name: "Add review item" }).click();
  await expect(page.getByText(title, { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByText(title, { exact: true })).toBeVisible();
});

test("project identity and audit data survive a new browser context", async ({ browser }) => {
  const context = await browser.newContext();
  const second = await context.newPage();
  await signIn(second);
  await second.goto("/settings/audit");
  await expect(second.getByRole("heading", { name: "Audit log" })).toBeVisible();
  await context.close();
});
