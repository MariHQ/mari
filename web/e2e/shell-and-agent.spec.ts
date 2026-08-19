import { expect, test } from "@playwright/test";
import { installMockApi } from "./fixtures/mock-api";

test.beforeEach(async ({ page }) => { await installMockApi(page); });

test("global search opens from the shell and supports keyboard result selection", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /Search knowledge/ })
    .or(page.getByRole("button", { name: "Search", exact: true })).click();
  const dialog = page.getByRole("dialog", { name: "Global search" });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("textbox", { name: "Search" }).fill("retention");
  await expect(dialog.getByRole("option", { name: /Retention runbook/ })).toBeVisible();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/knowledge\/doc\?id=1$/);
  await expect(page.getByRole("heading", { name: "Retention runbook" })).toBeVisible();
});

test("account navigation and logout work through the shell menu", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Account: Dana Rodriguez" }).click();
  await page.getByRole("menuitem", { name: "Preferences" }).click();
  await expect(page).toHaveURL(/\/preferences$/);

  await page.getByRole("button", { name: "Account: Dana Rodriguez" }).click();
  await page.getByRole("menuitem", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
});

test("agent SSE renders tools and warnings, navigates, and survives route changes", async ({ page }) => {
  await page.route("**/agent/chat", async (route) => {
    const body = [
      'event: meta\ndata: {"session_id":41}',
      'event: tool_start\ndata: {"name":"search","args":{"query":"audit"}}',
      'event: tool_result\ndata: {"name":"search","summary":"Found repository audit","ok":true}',
      'event: warning\ndata: {"message":"Using the latest completed audit."}',
      'event: navigate\ndata: {"path":"/audit"}',
      'event: token\ndata: {"token":"Opened the repository audit."}',
      'event: done\ndata: {"session_id":41}',
      "",
    ].join("\n\n");
    await route.fulfill({ status: 200, contentType: "text/event-stream", body });
  });

  await page.goto("/");
  await page.getByRole("button", { name: "Open the Mari agent" }).click();
  await expect(page.getByText("Mari agent", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Take me to the repository audit" }).click();
  await expect(page).toHaveURL(/\/audit$/);
  await expect(page.getByText("Opened the repository audit.")).toBeVisible();
  await expect(page.getByText("Found repository audit")).toBeVisible();
  await expect(page.getByText("Using the latest completed audit.")).toBeVisible();

  await page.getByRole("button", { name: "Close the Mari agent" }).click();
  await page.reload();
  await expect(page.getByRole("button", { name: "Open the Mari agent" })).toBeVisible();
});

test("agent network failure is explicit and keeps the composer usable", async ({ page }) => {
  await page.route("**/agent/chat", (route) => route.abort("failed"));
  await page.goto("/");
  await page.getByRole("button", { name: "Open the Mari agent" }).click();
  await page.getByPlaceholder("Ask Mari…").fill("Can you hear me?");
  await page.getByRole("button", { name: /Send/ }).click();
  await expect(page.getByText(/can't reach the Mari API/)).toBeVisible();
  await expect(page.getByPlaceholder("Ask Mari…")).toBeEnabled();
});
