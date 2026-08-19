import { expect, test } from "@playwright/test";
import { installMockApi } from "./fixtures/mock-api";

test("a review item opens the exact evidence recorded by its typed subject", async ({ page }) => {
  const api = await installMockApi(page);
  await page.goto("/tasks");

  await expect.poll(() => api.calls.some((call) =>
    call.query.includes("subjectType")
      && call.query.includes("subjectId")
      && call.query.includes("subjectTitle")
      && call.query.includes("subjectHref"),
  )).toBeTruthy();

  await page.getByText("Retention runbook", { exact: true }).click();
  await expect(page).toHaveURL(/\/knowledge\/doc\?id=1$/);
  await expect(page.getByText("Retention runbook", { exact: true }).first()).toBeVisible();
});
