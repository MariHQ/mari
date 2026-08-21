import { expect, test } from "@playwright/test";
import { installMockApi } from "./fixtures/mock-api";

test("Review renders mixed kinds, filters them, and deep-links to evidence", async ({ page }) => {
  const api = await installMockApi(page);
  await page.goto("/tasks");
  await expect(page.getByRole("heading", { name: "Review" })).toBeVisible();
  for (const title of ["Retention is 10 days", "Move derived vectors to object storage",
    "What is the deletion SLA?", "Conflicting retention duration",
    "Replace 10 days with 30 days", "Fact review approval"]) {
    await expect(page.getByText(title, { exact: true }).first()).toBeVisible();
  }

  await page.getByRole("combobox", { name: "Kind" }).last().selectOption("fact");
  await expect(page.getByText("Retention is 10 days", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Move derived vectors to object storage", { exact: true })).toBeHidden();
  await page.getByRole("button", { name: "Verify" }).click();
  await expect.poll(() => api.calls.some((call) =>
    call.query.includes("evaluateReviewItem") && call.variables.reviewId === "fact:2",
  )).toBeTruthy();
  await expect(page.getByRole("status")).toContainText("manual: More evidence is required.");
  await page.getByRole("button", { name: "Open fact: Retention is 10 days" }).click();
  await expect(page).toHaveURL(/\/facts\?fact=2$/);
});

test("Review exposes source and assignee filters on mobile", async ({ page }) => {
  await installMockApi(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/tasks");
  await expect(page.getByRole("combobox", { name: "Source" })).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Assignee" })).toBeVisible();
  await page.getByRole("combobox", { name: "Source" }).selectOption("automation");
  await expect(page.getByText("Fact review approval", { exact: true })).toBeVisible();
  await expect(page.getByText("Retention is 10 days", { exact: true })).toBeHidden();
});

test("Review bounds a large queue with paging", async ({ page }) => {
  const api = await installMockApi(page);
  api.setData("reviewItems", {
    items: Array.from({ length: 100 }, (_, i) => ({
      id: `fact:${i}`, kind: "fact", title: `Fact review ${i}`, status: "pending",
      source: "handbook", assignee: "Dana Rodriguez", due: "", subjectType: "fact",
      subjectId: String(i), subjectTitle: `Fact ${i}`, subjectHref: `/facts?fact=${i}`,
      confidence: 0, evidenceCount: 1, trustedSource: false,
    })),
    totalCount: 100, pageInfo: { endCursor: "MTAw", hasNextPage: false },
  });
  await page.goto("/tasks");
  await expect(page.getByText("Showing 1 to 25 of 100 open review items")).toBeVisible();
  await expect(page.getByText("Fact review 99", { exact: true })).toBeHidden();
  await page.getByRole("button", { name: "Next page" }).click();
  await expect(page.getByText("Fact review 25", { exact: true })).toBeVisible();
});

test("Review renders an empty state", async ({ page }) => {
  const api = await installMockApi(page);
  api.setData("reviewItems", { items: [], totalCount: 0, pageInfo: { endCursor: "MA", hasNextPage: false } });
  api.setData("tasksSummary", null);
  await page.goto("/tasks");
  await expect(page.getByText("Nothing open: all caught up.")).toBeVisible();

});

test("Review renders the server read error", async ({ page }) => {
  await installMockApi(page);
  await page.route("**/graphql", async (route) => {
    const query = String(route.request().postDataJSON()?.query ?? "");
    if (query.includes("reviewItems")) {
      await route.fulfill({ json: { errors: [{ message: "Review service unavailable" }] } });
    } else {
      await route.fallback();
    }
  });
  await page.goto("/tasks");
  await expect(page.getByText(/Review service unavailable/)).toBeVisible();
});
