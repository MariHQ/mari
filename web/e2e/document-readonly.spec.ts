import { expect, test } from "@playwright/test";
import { installMockApi } from "./fixtures/mock-api";

test.beforeEach(async ({ page }) => { await installMockApi(page); });

test("connector documents are read-only source records", async ({ page }) => {
  await page.goto("/knowledge/doc?id=1");

  await expect(page.getByRole("heading", { name: "Retention runbook" })).toBeVisible();
  await expect(page.getByText("Read-only source record")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Source content" })).toBeVisible();
  await expect(page.getByText("Retention is 30 days.")).toBeVisible();
  await expect(page.getByRole("button", { name: /^Save/ })).toHaveCount(0);
  await expect(page.getByText("Change queue", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Refine", { exact: true })).toHaveCount(0);
  await expect(page.locator("[contenteditable=true]")).toHaveCount(0);
});
