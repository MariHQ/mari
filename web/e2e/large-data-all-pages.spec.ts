import { expect, test } from "@playwright/test";
import { installMockApi, type MockApi } from "./fixtures/mock-api";

let api: MockApi;
test.beforeEach(async ({ page }) => { api = await installMockApi(page); });

async function expectNoPageOverflow(page: import("@playwright/test").Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(1);
}

test("a 1,500-task queue renders a bounded, navigable page", async ({ page }) => {
  api.setData("tasks", Array.from({ length: 1_500 }, (_, index) => ({
    id: index + 1, title: `Queue item ${index + 1}`, assigneeInitials: "DR",
    kind: "approval", kindLabel: "Approval", done: false, due: "2026-08-30", overdue: false,
  })));
  await page.goto("/tasks");
  await expect(page.getByText("Showing 1 to 25 of 1,500 open tasks")).toBeVisible();
  await expect(page.getByRole("button", { name: "Mark done" })).toHaveCount(25);
  await page.getByRole("button", { name: "Next page" }).click();
  await expect(page.getByText("Showing 26 to 50 of 1,500 open tasks")).toBeVisible();
  await expect(page.getByText("Queue item 26", { exact: true })).toBeVisible();
  await expectNoPageOverflow(page);
});

test("a 1,200-answer catalog renders 24 cards and preserves pagination", async ({ page }) => {
  api.setData("approvedAnswers", Array.from({ length: 1_200 }, (_, index) => ({
    id: index + 1, question: `Question ${index + 1}`, answer: `Grounded answer ${index + 1}`,
    status: "approved", owner: "Dana", channels: ["slack-bot"], sources: [], served: index,
    spark: [1, 2, 1], updated: "2026-08-19T12:00:00Z",
  })));
  await page.goto("/answers");
  await expect(page.getByText("Showing 1 to 24 of 1,200 answers")).toBeVisible();
  await expect(page.getByText(/^Question \d+$/)).toHaveCount(24);
  await page.getByRole("button", { name: "Next page" }).click();
  await expect(page.getByText("Question 25", { exact: true })).toBeVisible();
  await expect(page.getByText("Question 1", { exact: true })).toHaveCount(0);
  await expectNoPageOverflow(page);
});

test("large fact and member tables keep only one 25-row page in the DOM", async ({ page }) => {
  api.setData("facts", Array.from({ length: 1_000 }, (_, index) => ({
    id: index + 1, claim: `Verified claim ${index + 1}`, source: `Runbook ${index + 1}`,
    owner: "Dana", status: "Verified", verified: "2026-08-18",
  })));
  await page.goto("/facts");
  await expect(page.getByText("Showing 1 to 25 of 1,000 facts")).toBeVisible();
  await expect(page.getByRole("row")).toHaveCount(26);
  await expectNoPageOverflow(page);

  api.setData("members", Array.from({ length: 700 }, (_, index) => ({
    id: index + 1, name: `Member ${index + 1}`, email: `member-${index + 1}@example.test`,
    role: index % 10 === 0 ? "admin" : "user", initials: "MT", tint: 2,
    status: "active", joined: "2026-08-01", lastActive: "2026-08-19T12:00:00Z", provider: "password",
  })));
  await page.goto("/settings/members");
  await expect(page.getByText("Showing 1 to 12 of 700 members")).toBeVisible();
  await expect(page.getByRole("row")).toHaveCount(13);
  await expectNoPageOverflow(page);
});
