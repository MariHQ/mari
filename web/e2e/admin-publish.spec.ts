import { expect, test } from "@playwright/test";
import { installMockApi, type MockApi } from "./fixtures/mock-api";

let api: MockApi;
test.beforeEach(async ({ page }) => { api = await installMockApi(page); });

test("workspace admins can invite a user and configure GitHub team provisioning", async ({ page }) => {
  await page.goto("/settings/members");
  await expect(page.getByText(/Members of/)).toContainText("acme/docs");
  await page.getByRole("button", { name: "Invite member" }).click();
  await page.getByLabel("Name").fill("Rippling Test User");
  await page.getByLabel("Email", { exact: true }).fill("rippling-test@example.test");
  await page.getByLabel("Role", { exact: true }).selectOption("user");
  await page.getByRole("button", { name: "Send invite" }).click();
  await expect(page.getByTitle("rippling-test@example.test")).toBeVisible();
  expect(api.calls.some((c) => c.query.includes("inviteMember") && c.variables.email === "rippling-test@example.test")).toBeTruthy();

  const teamCard = page.getByText("GitHub team sync", { exact: true }).locator("xpath=ancestor::*[contains(@class,'rounded')][1]");
  await teamCard.getByRole("button", { name: "Configure" }).click();
  await teamCard.getByLabel("Team slug").fill("rippling/knowledge");
  await teamCard.getByRole("button", { name: "Save" }).click();
  expect(api.calls.some((c) => c.query.includes("setGithubTeam") && c.variables.team === "rippling/knowledge")).toBeTruthy();
  await expect(page.getByText("SCIM", { exact: true })).toBeVisible();
  await expect(page.getByText("Enterprise", { exact: true })).toBeVisible();
});

test("deployment target settings and site deploy are wired through the browser", async ({ page }) => {
  await page.goto("/publish?site=1");
  await page.getByRole("button", { name: "Domains" }).click();
  await page.getByLabel("Bucket").fill("rippling-docs-e2e");
  await page.getByLabel("Region").fill("us-west-2");
  await page.getByRole("button", { name: "Save deploy config" }).click();
  await expect(page.getByText("✓ Saved", { exact: true })).toBeVisible();
  expect(api.calls.some((c) => c.query.includes("updateSetting") && c.variables.key === "deploy")).toBeTruthy();

  await page.getByRole("button", { name: "Deploy", exact: true }).click();
  await expect(page.getByRole("button", { name: "Deployed" })).toBeVisible();
  expect(api.calls.some((c) => c.query.includes("deploySite") && c.variables.id === 1)).toBeTruthy();
});

test("OSS browser messaging exposes standard MCP and does not promote a Claude plugin", async ({ page }) => {
  for (const path of ["/", "/publish", "/publish?tab=mcp", "/settings/models"]) {
    await page.goto(path);
    await expect(page.locator("body")).not.toContainText(/Claude plugin|Claude Code plugin/i);
  }
  await page.goto("/publish?tab=mcp");
  await expect(page.getByText(/AI tools and agents/).first()).toBeVisible();
  await page.getByRole("button", { name: "How to connect" }).click();
  await expect(page.getByText(/mcpServers/)).toBeVisible();
});
