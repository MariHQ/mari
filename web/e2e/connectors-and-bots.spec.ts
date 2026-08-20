import { expect, test } from "@playwright/test";
import { installMockApi, type MockApi } from "./fixtures/mock-api";

let api: MockApi;
test.beforeEach(async ({ page }) => {
  api = await installMockApi(page);
});

async function openSources(page: import("@playwright/test").Page) {
  await page.goto("/sources");
  await expect(page.getByRole("heading", { name: "Sources" })).toBeVisible();
}

async function openBotsDestination(page: import("@playwright/test").Page) {
  await page.goto("/publish?tab=bots");
  await expect(page.getByRole("heading", { name: "Destinations" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Bots", exact: true })).toHaveAttribute("aria-pressed", "true");
}

const connectors = [
  { name: "Confluence", fields: { "Site URL": "https://acme.atlassian.test", "Atlassian account email": "docs@example.test", "API token": "atl-secret" }, transport: "rest" },
  { name: "Slack", fields: { "Bot token": "xoxb-browser-test", "Channels": "engineering" }, transport: "rest" },
  { name: "Google Drive", fields: { "OAuth2 access token": "ya29.browser-test", "Folder ID": "folder-1" }, transport: "rest" },
  { name: "GitHub", fields: { "Personal access token": "github_pat_browser", "Repository": "acme/handbook" }, transport: "graphql" },
] as const;

for (const connector of connectors) {
  test(`${connector.name} credentials validate and start polling from the browser`, async ({ page }) => {
    await openSources(page);
    await page.getByRole("button", { name: "Add source" }).click();
    const dialog = page.getByRole("dialog", { name: /Connect/ });
    await dialog.getByRole("button", { name: new RegExp(connector.name) }).click();
    await dialog.getByRole("button", { name: "Next" }).click();
    for (const [label, value] of Object.entries(connector.fields)) {
      await dialog.getByLabel(label).fill(value);
    }
    const secret = dialog.locator('input[type="password"]');
    await expect(secret.first()).toHaveAttribute("type", "password");
    await dialog.getByRole("button", { name: "Test connection" }).click();
    await expect(dialog.getByText(/Connection OK/)).toBeVisible();
    await dialog.getByRole("button", { name: "Connect & sync" }).click();
    await expect(dialog.getByText(/initial sync runs on the server/i)).toBeVisible();

    expect(api.restCalls.some((c) => c.path === "/connectors/validate" && c.body.provider === connector.name.toLowerCase().replace("google drive", "gdrive"))).toBeTruthy();
    if (connector.transport === "rest") {
      expect(api.restCalls.some((c) => c.path === "/connectors/connect")).toBeTruthy();
    } else {
      expect(api.calls.some((c) => c.query.includes("connectGithubRepo") && c.variables.repo === "acme/handbook")).toBeTruthy();
    }
  });
}

test("connected sources can request incremental and full polls", async ({ page }) => {
  await openSources(page);
  await page.getByRole("button", { name: "Actions for acme/handbook" }).click();
  await page.getByRole("menuitem", { name: "Sync now" }).click();
  await expect.poll(() => api.calls.some((c) => c.query.includes("syncSource"))).toBeTruthy();
  await page.getByRole("button", { name: "Actions for Confluence — ENG" }).click();
  await page.getByRole("menuitem", { name: "Full resync" }).click();
  await expect.poll(() => api.calls.some((c) => c.query.includes("resyncSource"))).toBeTruthy();
});

test("Sources exposes connector ingestion without a Bots tab", async ({ page }) => {
  await openSources(page);
  await expect(page.getByRole("button", { name: "Add source" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Bots", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Connectors", exact: true })).toHaveCount(0);
});

test("Slack bot setup saves secrets, calls auth.test, and never renders the token", async ({ page }) => {
  await openBotsDestination(page);
  await page.getByRole("button", { name: "Manage setup" }).first().click();
  const drawer = page.getByRole("dialog", { name: "Set up Slack bot" });
  await drawer.getByRole("button", { name: "Next" }).click();
  await drawer.getByRole("textbox", { name: "Bot token" }).fill("xoxb-browser-secret  ");
  await drawer.getByRole("textbox", { name: "Signing secret" }).fill("signing-browser-secret");
  await drawer.getByRole("button", { name: "Save credentials" }).click();
  await expect(drawer.getByText("Saved", { exact: true })).toBeVisible();
  await drawer.getByRole("button", { name: "Next" }).click();
  await drawer.getByRole("button", { name: "Test connection" }).click();
  await expect(drawer.getByText(/Connected in Acme/)).toBeVisible();
  await expect(page.getByText("xoxb-browser-secret", { exact: true })).toHaveCount(0);
  expect(api.calls.some((c) => c.query.includes("updateSetting") && c.variables.key === "slack_bot")).toBeTruthy();
  const saved = api.calls.find((c) => c.query.includes("updateSetting") && c.variables.key === "slack_bot");
  expect((saved?.variables.value as any).bot_token).toBe("xoxb-browser-secret");
  expect(api.restCalls.some((c) => c.path === "/bots/slack/test")).toBeTruthy();
});

test("GitHub webhook setup persists a generated signing secret and observes delivery", async ({ page }) => {
  await openBotsDestination(page);
  await page.getByRole("button", { name: "Manage setup" }).nth(1).click();
  const drawer = page.getByRole("dialog", { name: "Set up GitHub webhook" });
  await expect(drawer.getByText(/webhooks\/github/)).toBeVisible();
  await drawer.getByRole("button", { name: "Next" }).click();
  await drawer.getByRole("button", { name: "Generate" }).click();
  await drawer.getByRole("button", { name: "Save secret" }).click();
  await expect(drawer.getByText("Saved", { exact: true })).toBeVisible();
  await drawer.getByRole("button", { name: "Next" }).click();
  await expect(drawer.getByText(/Delivery received/)).toBeVisible();
  expect(api.calls.some((c) => c.query.includes("updateSetting") && c.variables.key === "github_bot")).toBeTruthy();
});
