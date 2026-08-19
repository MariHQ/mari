import { expect, test } from "@playwright/test";
import { installMockApi, type MockApi } from "./fixtures/mock-api";

let api: MockApi;
test.beforeEach(async ({ page }) => { api = await installMockApi(page); });

test("tasks can be created, completed, filtered, and report write failures", async ({ page }) => {
  await page.goto("/tasks");
  await page.getByRole("textbox", { name: "Task", exact: true }).fill("Review the deletion SLA");
  await page.getByLabel("Kind").selectOption("approval");
  await page.getByLabel("Due date").fill("2026-08-29");
  await page.getByRole("button", { name: "Add task" }).click();
  await expect.poll(() => api.calls.some((call) => call.query.includes("createTask") && call.variables.title === "Review the deletion SLA")).toBeTruthy();

  await page.getByRole("button", { name: "Mark done" }).first().click();
  await expect.poll(() => api.calls.some((call) => call.query.includes("setTaskDone") && call.variables.done === true)).toBeTruthy();
  await page.getByRole("button", { name: /Assigned to me/ }).click();
  await expect(page.getByRole("button", { name: /Assigned to me/ })).toHaveAttribute("aria-pressed", "true");

  api.failNext(/createTask/, "The work queue is unavailable");
  await page.getByRole("textbox", { name: "Task", exact: true }).fill("This write should remain visible");
  await page.getByRole("button", { name: "Add task" }).click();
  await expect(page.getByText("The work queue is unavailable")).toBeVisible();
  await expect(page.getByRole("textbox", { name: "Task", exact: true })).toHaveValue("This write should remain visible");
});

test("decisions can be captured, ratified with confirmation, and impact-analysed", async ({ page }) => {
  api.setData("decisions", [{
    id: 7, statement: "Store canonical records in Iceberg", context: "Scale analytical reads.",
    status: "proposed", sourceLabel: "Mari", owners: ["Dana"], decidedOn: "2026-08-19",
    supersededBy: null, supersededByStatement: "", impactSummary: "", impactCount: 0,
  }]);
  await page.goto("/decisions");
  await page.getByRole("button", { name: "Capture decision" }).click();
  await page.getByLabel("Statement").fill("Use a durable queue for scheduled work");
  await page.getByLabel("Context").fill("Workers must scale independently.");
  await page.getByLabel("Source").fill("Architecture review");
  await page.getByRole("button", { name: "Capture", exact: true }).click();
  await expect.poll(() => api.calls.some((call) => call.query.includes("addDecision") && call.variables.statement === "Use a durable queue for scheduled work")).toBeTruthy();

  const ratify = page.getByRole("button", { name: "Ratify", exact: true }).first();
  await ratify.click();
  await page.getByRole("button", { name: "Ratify this decision?" }).first().click();
  await expect.poll(() => api.calls.some((call) => call.query.includes("ratifyDecision") && call.variables.id === 7)).toBeTruthy();

  const impact = page.getByRole("button", { name: /impact/i }).first();
  if (await impact.count()) {
    await impact.click();
    await expect(page.getByText("One runbook is affected.")).toBeVisible();
    expect(api.calls.some((call) => call.query.includes("decisionImpact"))).toBeTruthy();
  }
});

test("answers can be drafted and harvested from selected sources", async ({ page }) => {
  await page.goto("/answers");
  await page.getByRole("button", { name: "New answer" }).click();
  await page.getByPlaceholder("Question people ask").fill("What is the deletion SLA?");
  await page.getByPlaceholder("The wording to serve, verbatim").fill("Deletion completes within seven days.");
  await page.getByRole("button", { name: "Save draft" }).click();
  await expect(page.getByText(/Saved as a draft/)).toBeVisible();
  expect(api.calls.some((call) => call.query.includes("upsertAnswer") && call.variables.question === "What is the deletion SLA?")).toBeTruthy();

  await page.getByRole("button", { name: "Done" }).click();
  await page.getByRole("button", { name: /Harvest questions/ }).click();
  await page.getByLabel(/Slack/).check();
  await page.getByRole("button", { name: /Scan \d+ sources?/ }).click();
  await expect(page.getByText("How long is deletion?")).toBeVisible();
  expect(api.calls.some((call) => call.query.includes("scanAnswerCandidates"))).toBeTruthy();
});

test("API keys reveal a new secret once and can be revoked", async ({ page }) => {
  await page.goto("/settings/api-keys");
  await page.getByRole("button", { name: "Create key" }).click();
  await page.getByLabel("Name").fill("Browser acceptance");
  await page.getByRole("button", { name: "Create key", exact: true }).last().click();
  await page.getByRole("button", { name: "Reveal token" }).click();
  await expect(page.getByText("mari_browser_secret_once", { exact: true })).toBeVisible();
  expect(api.calls.some((call) => call.query.includes("createApiKey"))).toBeTruthy();
  await page.getByRole("button", { name: "Dismiss" }).click();
  await expect(page.getByText("mari_browser_secret_once", { exact: true })).toHaveCount(0);

  await page.getByRole("button", { name: "Revoke", exact: true }).first().click();
  await page.getByRole("button", { name: "Revoke?" }).first().click();
  await expect.poll(() => api.calls.some((call) => call.query.includes("revokeApiKey") && call.variables.id === 1)).toBeTruthy();
});

test("profile, password, and notification settings reach their account endpoints", async ({ page }) => {
  await page.goto("/preferences");
  await page.getByLabel("Display name").fill("Dana Browser");
  await page.getByRole("button", { name: "Save profile" }).click();
  await expect(page.getByText("Profile saved", { exact: true })).toBeVisible();

  await page.getByLabel("Current password").fill("old-password");
  await page.getByRole("textbox", { name: /New password/ }).fill("new-password-123");
  await page.getByLabel("Confirm new password").fill("new-password-123");
  await page.getByRole("button", { name: "Change password" }).click();
  await expect(page.getByText(/Password changed/)).toBeVisible();

  await page.getByRole("switch", { name: "Mentions and review requests" }).click();
  await expect.poll(() => api.restCalls.filter((call) => call.path.startsWith("/auth/preferences/")).length).toBeGreaterThanOrEqual(3);
});
