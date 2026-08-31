import { expect, test } from "@playwright/test";
import { installMockApi, type MockApi } from "./fixtures/mock-api";

let api: MockApi;
test.beforeEach(async ({ page }) => { api = await installMockApi(page); });

test("facts can be verified and captured through the ledger", async ({ page }) => {
  await page.goto("/facts");
  const stale = page.getByRole("row").filter({ hasText: "Retention is 10 days." });
  await stale.getByRole("button", { name: "Verify" }).click();
  await expect(stale.getByText("Verified", { exact: true })).toBeVisible();
  expect(api.calls.some((c) => c.query.includes("verifyFact") && c.variables.id === 2)).toBeTruthy();

  await page.getByRole("button", { name: "New fact" }).click();
  const drawer = page.getByRole("dialog", { name: "New fact" });
  await drawer.getByLabel("Claim").fill("Deletion requests finish within 7 days.");
  await drawer.getByLabel("Where it comes from").fill("Deletion runbook");
  await drawer.getByLabel("Owner").fill("Dana");
  await drawer.getByRole("button", { name: "Add fact" }).click();
  await expect(drawer).toBeHidden();
  expect(api.calls.some((c) => c.query.includes("addFact") && c.variables.claim === "Deletion requests finish within 7 days.")).toBeTruthy();
});

test("high-impact facts expose temporal evidence before invalidation", async ({ page }) => {
  await page.goto("/facts");
  const row = page.getByRole("row").filter({ hasText: "Retention is 30 days." });
  await expect(row.getByText("High impact", { exact: true })).toBeVisible();
  await row.getByRole("button", { name: "Impact" }).click();
  const drawer = page.getByRole("dialog", { name: "Fact impact neighborhood" });
  await expect(drawer.getByText("Retention is 10 days.", { exact: true })).toBeVisible();
  await expect(drawer.getByText("contradicts", { exact: true })).toBeVisible();
  await drawer.getByRole("button", { name: "Close" }).click();
  await row.getByRole("button", { name: "Invalidate" }).click();
  await page.getByRole("button", { name: "Invalidate this claim and preserve its impact history?" }).click();
  await expect(row.getByText("Invalidated", { exact: true })).toBeVisible();
  expect(api.calls.some((call) => call.query.includes("invalidateFact"))).toBeTruthy();
});

test("LLM fact scan starts a workflow and reports its grounded result", async ({ page }) => {
  await page.goto("/facts");
  await page.getByRole("button", { name: "Scan for facts" }).click();
  const config = page.getByRole("dialog", { name: "Configure fact extraction" });
  await config.getByLabel("Search within documents").fill("infrastructure");
  await config.getByLabel("Documents per run").fill("25");
  await config.getByLabel("Review strategy").selectOption("auto");
  await expect(config.getByText("High-confidence recommendations are applied; uncertain candidates still wait for you"))
    .toBeVisible();
  await config.getByRole("button", { name: "Save & run now" }).click();
  await expect(page.getByText(/Fact scan · run #1900/)).toBeVisible();
  await expect(page.getByText("2 new claims captured", { exact: true })).toBeVisible();
  await expect(page.getByText("AI recommendation", { exact: true })).toBeVisible();
  await expect(page.getByText("Accept as a new fact", { exact: true })).toBeVisible();
  expect(api.calls.some((c) => c.query.includes("startFactScan")
    && c.variables.config.query === "infrastructure"
    && c.variables.config.limit === 25
    && c.variables.config.adjudication_mode === "llm"
    && c.variables.config.review_mode === "ai")).toBeTruthy();
  expect(api.calls.some((c) => c.query.includes("workflowRun"))).toBeTruthy();
  // The run must survive a reload through the recovery path before a
  // dismissal, or the "stays dismissed" assertion below proves nothing.
  await page.reload();
  await expect(page.getByText(/Fact scan · run #1900/)).toBeVisible();
  await page.getByRole("button", { name: "Dismiss" }).click();
  await expect(page.getByText(/Fact scan · run #1900/)).toHaveCount(0);
  expect(api.calls.some((c) => c.query.includes("dismissWorkflowRun") && c.variables.runId === 99)).toBeTruthy();
  await page.reload();
  await expect(page.getByText(/Fact scan · run #1900/)).toHaveCount(0);
});

test("fact write failures remain visible and do not close the form", async ({ page }) => {
  api.failNext(/addFact/, "Fact already exists");
  await page.goto("/facts");
  await page.getByRole("button", { name: "New fact" }).click();
  const drawer = page.getByRole("dialog", { name: "New fact" });
  await drawer.getByLabel("Claim").fill("Retention is 30 days.");
  await drawer.getByLabel("Where it comes from").fill("Runbook");
  await drawer.getByRole("button", { name: "Add fact" }).click();
  await expect(drawer.getByText("Fact already exists")).toBeVisible();
  await expect(drawer).toBeVisible();
});

test("MCP publishing creates a scoped endpoint and reveals its token only once", async ({ page }) => {
  await page.goto("/publish?tab=mcp");
  await expect(page.getByText("MCP servers", { exact: true }).first()).toBeVisible();
  await page.getByRole("button", { name: "New server" }).click();
  await page.getByLabel("Name").fill("browser-agent");
  await page.getByRole("button", { name: "Create server" }).click();
  await page.getByRole("button", { name: "Reveal token" }).click();
  await expect(page.getByText("mari_mcp_browser_test", { exact: true })).toBeVisible();
  expect(api.calls.some((c) => c.query.includes("createMcpServer") && c.variables.name === "browser-agent")).toBeTruthy();
});

test("an MCP endpoint can be health-checked and configured from the browser", async ({ page }) => {
  await page.goto("/publish?tab=mcp");
  await page.getByRole("button", { name: "Test", exact: true }).click();
  await expect(page.getByText(/^Connected/)).toBeVisible();
  await expect(page.getByText(/search ✓ 1/)).toBeVisible();
  await page.getByRole("button", { name: "How to connect" }).click();
  await expect(page.getByText(/mcpServers/)).toBeVisible();
  await expect(page.getByText(/claude/i)).toHaveCount(0);
  expect(api.calls.some((c) => c.query.includes("testMcpServer") && c.variables.id === 1)).toBeTruthy();
});
