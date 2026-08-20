import { expect, test } from "@playwright/test";
import { installMockApi, type MockApi } from "./fixtures/mock-api";

let api: MockApi;
test.beforeEach(async ({ page }) => { api = await installMockApi(page); });

test("admin creates, configures, and deploys a knowledge chat destination", async ({ page }) => {
  await page.goto("/publish?tab=chat");
  await expect(page.getByRole("button", { name: "Knowledge chat", exact: true })).toHaveAttribute("aria-pressed", "true");
  await page.getByLabel("Destination name").fill("Company knowledge");
  await page.getByLabel("URL slug").fill("company-knowledge");
  await page.getByLabel("Assistant title").fill("Ask Acme");
  await page.getByLabel("Welcome message").fill("Ask about company policy.");
  await page.getByRole("button", { name: "Create knowledge chat" }).click();
  await expect(page).toHaveURL(/tab=chat&chat=7/);
  expect(api.calls.some((call) => call.query.includes("createKnowledgeChatDestination") && call.variables.slug === "company-knowledge")).toBeTruthy();

  await page.getByLabel("Welcome message").fill("Ask about trusted company policy.");
  await page.getByRole("button", { name: "Save configuration" }).click();
  expect(api.calls.some((call) => call.query.includes("updateKnowledgeChatDestination") && call.variables.welcome === "Ask about trusted company policy.")).toBeTruthy();
  await page.getByRole("button", { name: "Deploy", exact: true }).click();
  expect(api.calls.some((call) => call.query.includes("deployKnowledgeChatDestination") && call.variables.id === 7)).toBeTruthy();
});

test("deployed knowledge chat streams an answer and cited sources", async ({ page }) => {
  await page.goto("/knowledge-chat/default/company-knowledge");
  await expect(page.getByRole("heading", { name: "Ask Acme" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Open the Mari agent" })).toHaveCount(0);
  await page.getByLabel("Ask a question").fill("How long is retention?");
  await page.getByRole("button", { name: "Ask" }).click();
  await expect(page.getByText("Retention is 30 days [1].")).toBeVisible();
  await expect(page.getByRole("list", { name: "Sources" })).toContainText("Retention runbook");
  expect(api.restCalls.some((call) => call.path === "/chat" && call.body.message === "How long is retention?")).toBeTruthy();
  await page.getByRole("link", { name: "[1] Retention runbook" }).click();
  await expect(page).toHaveURL(/\/knowledge\/doc\?id=1/);
});

test("knowledge chat admin and end-user surfaces remain usable on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/publish?tab=chat");
  await expect(page.getByLabel("Destination name")).toBeVisible();
  await page.goto("/knowledge-chat/default/company-knowledge");
  await expect(page.getByLabel("Ask a question")).toBeVisible();
  await expect(page.locator("body")).not.toHaveCSS("overflow-x", "scroll");
});
