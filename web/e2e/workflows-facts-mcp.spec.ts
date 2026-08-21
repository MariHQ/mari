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

test("LLM fact scan starts a workflow and reports its grounded result", async ({ page }) => {
  await page.goto("/facts");
  await page.getByRole("button", { name: "Scan for facts" }).click();
  await expect(page.getByText(/Fact scan · run #1900/)).toBeVisible();
  await expect(page.getByText(/2 new claims captured/)).toBeVisible();
  expect(api.calls.some((c) => c.query.includes("startFactScan"))).toBeTruthy();
  expect(api.calls.some((c) => c.query.includes("workflowRun"))).toBeTruthy();
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

test("flows support real runs, dry runs, pausing, and creation handoff", async ({ page }) => {
  await page.goto("/flows");
  const flow = page.getByRole("row").filter({ hasText: "Fact review" });
  await flow.getByRole("button", { name: "Run", exact: true }).click();
  await flow.getByRole("button", { name: "Test run" }).click();
  await flow.getByRole("switch", { name: /Fact review/ }).click();
  await expect.poll(() => api.calls.filter((c) => c.query.includes("runWorkflow")).length).toBe(2);
  expect(api.calls.some((c) => c.query.includes("runWorkflow") && c.variables.dryRun === true)).toBeTruthy();
  expect(api.calls.some((c) => c.query.includes("setWorkflowStatus") && c.variables.status === "paused")).toBeTruthy();

  await page.getByRole("button", { name: "New automation" }).click();
  const drawer = page.getByRole("dialog", { name: "New automation" });
  await drawer.getByLabel("Name").fill("Browser acceptance flow");
  await drawer.getByLabel("What does it guarantee?").fill("Every workflow path remains executable.");
  await drawer.getByRole("button", { name: "Create and open editor" }).click();
  await expect(page).toHaveURL(/\/flows\?flow=2$/);
  expect(api.calls.some((c) => c.query.includes("saveWorkflow") && c.variables.name === "Browser acceptance flow")).toBeTruthy();
  expect(api.calls.some((c) => c.query.includes("setWorkflowTrigger") && c.variables.id === 2)).toBeTruthy();
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
