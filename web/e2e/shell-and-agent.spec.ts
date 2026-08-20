import { expect, test } from "@playwright/test";
import { installMockApi, type MockApi } from "./fixtures/mock-api";

let api: MockApi;
test.beforeEach(async ({ page }) => { api = await installMockApi(page); });

test("global search opens from the shell and supports keyboard result selection", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /Search knowledge/ })
    .or(page.getByRole("button", { name: "Search", exact: true })).click();
  const dialog = page.getByRole("dialog", { name: "Global search" });
  await expect(dialog).toBeVisible();
  const search = dialog.getByRole("combobox", { name: "Search" });
  await expect(search).toHaveAttribute("aria-expanded", "true");
  await search.fill("retention");
  const option = dialog.getByRole("option", { name: /Retention runbook/ });
  await expect(option).toBeVisible();
  await expect(search).toHaveAttribute("aria-controls", await dialog.getByRole("listbox", { name: "Search results" }).getAttribute("id") ?? "");
  await expect(search).toHaveAttribute("aria-activedescendant", await option.getAttribute("id") ?? "");
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
  await page.getByPlaceholder("Ask Mari…").fill("Take me to the repository audit");
  await page.getByRole("button", { name: /Send/ }).click();
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

test("agent accepts multiline SSE at EOF and rejects same-tick duplicate sends", async ({ page }) => {
  let posts = 0;
  await page.route("**/agent/chat", async (route) => {
    posts += 1;
    // JSON split across legal multi-line SSE data fields, with the final frame
    // deliberately not terminated by a blank line.
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: 'event: token\ndata: {"token":\ndata: "multiline works"}\n\nevent: done\ndata: {"session_id":9}',
    });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Open the Mari agent" }).click();
  const composer = page.getByPlaceholder("Ask Mari…");
  await composer.fill("One request only");
  await composer.press("Enter");
  await composer.press("Enter");
  await expect(page.getByText("multiline works", { exact: true })).toBeVisible();
  expect(posts).toBe(1);
});

test("agent preserves received text when a stream fails partway through", async ({ page }) => {
  await page.goto("/");
  await page.evaluate(() => {
    const original = window.fetch.bind(window);
    window.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/agent/chat")) {
        const stream = new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(new TextEncoder().encode(
              'event: token\ndata: {"token":"Useful partial answer"}\n\n',
            ));
            window.setTimeout(() => controller.error(new Error("connection reset")), 20);
          },
        });
        return Promise.resolve(new Response(stream, { status: 200, headers: { "Content-Type": "text/event-stream" } }));
      }
      return original(input, init);
    }) as typeof window.fetch;
  });
  await page.getByRole("button", { name: "Open the Mari agent" }).click();
  await page.getByPlaceholder("Ask Mari…").fill("Start an answer");
  await page.getByRole("button", { name: /Send/ }).click();
  await expect(page.getByText(/Useful partial answer/)).toBeVisible();
  await expect(page.getByText(/can't reach the Mari API/)).toBeVisible();
});

test("a transient auth check does not evict a confirmed signed-in session", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Account: Dana Rodriguez" })).toBeVisible();
  api.failNextAuthCheck(503);
  let first = true;
  await page.route("**/graphql", async (route) => {
    if (first) {
      first = false;
      await route.fulfill({ status: 401, json: { detail: "recheck" } });
    } else {
      await route.fallback();
    }
  });
  const menu = page.getByRole("button", { name: "Menu" });
  if (await menu.isVisible().catch(() => false)) await menu.click();
  await page.getByRole("navigation", { name: "Primary" })
    .getByRole("button", { name: "Analytics", exact: true }).click();
  await expect(page).toHaveURL(/\/insights$/);
  await expect(page.getByRole("button", { name: "Account: Dana Rodriguez" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Insights" })).toBeVisible();
});
