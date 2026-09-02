import { expect, test } from "@playwright/test";
import { installMockApi, NOT_A_CONNECTOR, type MockApi } from "./fixtures/mock-api";

let api: MockApi;
test.beforeEach(async ({ page }) => {
  api = await installMockApi(page);
});

test("Sources offers both connector ingestion and direct file upload", async ({ page }) => {
  // Upload was removed from this page once and reinstated 2026-09-01: with
  // it gone, the only way to add a file was the onboarding flow, once. The
  // control lives on the upload source's own card, beside Pause.
  await page.goto("/sources");
  await expect(page.getByRole("button", { name: "Upload files" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Add source" })).toBeVisible();
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
  { name: "Confluence", fields: { "Site URL": "https://acme.atlassian.test", "Atlassian account email": "docs@example.test", "API token": "atl-secret" } },
  { name: "Slack", fields: { "Bot token": "xoxb-browser-test", "Channels": "engineering" } },
  { name: "Google Drive", fields: { "OAuth2 access token": "ya29.browser-test", "Folder ID": "folder-1" } },
  { name: "GitHub", fields: { "Personal access token": "github_pat_browser", "Repository": "acme/handbook" } },
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
    expect(api.restCalls.some((c) => c.path === "/connectors/connect"
      && c.body.provider === connector.name.toLowerCase().replace("google drive", "gdrive"))).toBeTruthy();
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

test("Sources renders the scheduler's ten-minute connector cadence", async ({ page }) => {
  await openSources(page);
  const schedule = page.getByRole("combobox", { name: "Sync schedule for acme/handbook" });
  await expect(schedule).toHaveValue("10");
  await expect(schedule.getByRole("option", { name: "Every 10 minutes" })).toHaveCount(1);
});

test("pausing a source is labelled as a pause, not a destructive disconnect", async ({ page }) => {
  await openSources(page);
  await expect(page.getByRole("button", { name: "Disconnect", exact: true })).toHaveCount(0);
  const pause = page.getByRole("button", { name: "Pause", exact: true }).first();
  await pause.click();
  await page.getByRole("button", { name: "Pause this source?", exact: true }).click();
  await expect.poll(() => api.calls.some((c) => c.query.includes("pauseSource"))).toBeTruthy();
});

test("removing a source lets the admin retain its indexed documents", async ({ page }) => {
  await openSources(page);
  await page.getByRole("button", { name: "Actions for Confluence — ENG" }).click();
  await page.getByRole("menuitem", { name: "Remove…" }).click();
  const dialog = page.getByRole("dialog", { name: "Remove source" });
  await dialog.getByLabel("Keep indexed documents").check();
  await dialog.getByRole("button", { name: "Remove source" }).click();
  await expect.poll(() => api.calls.some((call) => call.query.includes("removeSource")
    && call.variables.deleteDocuments === false)).toBeTruthy();
});

test("files upload straight from the upload source's card", async ({ page }) => {
  await openSources(page);
  // The control was defined, documented, and never mounted: the only upload
  // path was the onboarding flow, once. It lives on the Uploads card now.
  await page.getByLabel("Choose files to upload").setInputFiles({
    name: "runbook.md", mimeType: "text/markdown", buffer: Buffer.from("# Runbook\nRetention is 30 days."),
  });
  await expect.poll(() => api.restCalls.some((call) => call.path === "/onboard/upload")).toBeTruthy();
  await expect(page.getByText("1 file ingested.", { exact: true })).toBeVisible();
});

test("removing a source deletes its documents by default", async ({ page }) => {
  await openSources(page);
  await page.getByRole("button", { name: "Actions for Confluence — ENG" }).click();
  await page.getByRole("menuitem", { name: "Remove…" }).click();
  const dialog = page.getByRole("dialog", { name: "Remove source" });
  await expect(dialog.getByLabel("Delete indexed documents")).toBeChecked();
  await dialog.getByRole("button", { name: "Remove source" }).click();
  await expect.poll(() => api.calls.some((call) => call.query.includes("removeSource")
    && call.variables.deleteDocuments === true)).toBeTruthy();
});

test("an orphan legacy source can be removed from its card", async ({ page }) => {
  // A row the retired connectSource mutation wrote: no kind, no documents,
  // no sync. It used to render without a Remove action and could not leave.
  await openSources(page);
  const card = page.getByRole("button", { name: "Actions for Confluence (old)" });
  await card.click();
  // The real orphan card: its provider is a catalog key, so the menu carries
  // the Edit entry the server refuses, not a stripped-down card the console
  // never draws.
  await expect(page.getByRole("menuitem", { name: "Edit connection" })).toBeVisible();
  await page.getByRole("menuitem", { name: "Remove…" }).click();
  const dialog = page.getByRole("dialog", { name: "Remove source" });
  await dialog.getByRole("button", { name: "Remove source" }).click();
  await expect.poll(() => api.calls.some((call) => call.query.includes("removeSource")
    && call.variables.id === 4)).toBeTruthy();
  // Removed for real: the card is gone once the page re-reads its sources.
  await expect(dialog).toHaveCount(0);
  await expect(card).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Actions for Confluence — ENG" })).toBeVisible();
});

test("an orphan legacy source shows the server's refusal instead of a fake sync", async ({ page }) => {
  // Every other menu entry on the orphan is refused server-side; the card
  // shows those words rather than a progress bar for a sync that never ran.
  await openSources(page);
  await page.getByRole("button", { name: "Actions for Confluence (old)" }).click();
  await page.getByRole("menuitem", { name: "Full resync" }).click();
  await expect.poll(() => api.calls.some((call) => call.query.includes("resyncSource")
    && call.variables.id === 4)).toBeTruthy();
  await expect(page.getByText(NOT_A_CONNECTOR, { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Actions for Confluence (old)" })).toBeVisible();
});

test("Sources exposes connector ingestion without a Bots tab", async ({ page }) => {
  await openSources(page);
  await expect(page.getByRole("button", { name: "Add source" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Bots", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Connectors", exact: true })).toHaveCount(0);
});

test("Slack bot setup persists the verified project installation, calls auth.test, and never renders the token", async ({ page }) => {
  api.setData("botsStatus", {
    slack: { configured: false, teamName: "", lastEventAt: null, lastError: null },
    github: { webhookConfigured: true, lastDeliveryAt: null, sources: [{ id: 1, repo: "acme/handbook" }] },
  });
  await openBotsDestination(page);
  await page.getByRole("button", { name: "Set up Slack bot" }).click();
  const drawer = page.getByRole("dialog", { name: "Set up Slack bot" });
  await expect(drawer.getByText("https://mari.example.test/webhooks/slack", { exact: false })).toBeVisible();
  await expect(drawer).toContainText("channels:history");
  await expect(drawer).toContainText("im:write");
  await expect(drawer).toContainText("message.channels");
  await expect(drawer).toContainText("messages_tab_enabled: true");
  await expect(drawer).toContainText("messages_tab_read_only_enabled: false");
  await expect(drawer).toContainText("socket_mode_enabled: true");
  expect(api.restCalls.some((call) => call.path === "/bots/slack/manifest")).toBeTruthy();
  await drawer.getByRole("button", { name: "Next" }).click();
  await drawer.getByRole("textbox", { name: "Bot token" }).fill("xoxb-browser-secret  ");
  await drawer.getByRole("textbox", { name: "App-level token" }).fill("xapp-browser-secret  ");
  await drawer.getByRole("textbox", { name: "Signing secret" }).fill("signing-browser-secret");
  await drawer.getByRole("button", { name: "Save credentials" }).click();
  await expect(drawer.getByText("Saved", { exact: true })).toBeVisible();
  await drawer.getByRole("button", { name: "Next" }).click();
  await drawer.getByRole("button", { name: "Test connection" }).click();
  await expect(drawer.getByText(/Connected in Acme/)).toBeVisible();
  await drawer.getByRole("button", { name: "Next" }).click();
  await expect(drawer.getByText("Waiting for first event", { exact: true })).toBeVisible();
  await expect(drawer).toContainText("App Home → Messages Tab");
  await drawer.getByRole("button", { name: "Done" }).click();
  const waiting = page.getByText("Waiting for first event", { exact: true });
  await expect(waiting).toHaveCount(2);
  await expect(waiting.first()).toBeVisible();
  await expect(page.getByText("Acme", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("xoxb-browser-secret", { exact: true })).toHaveCount(0);
  const setup = api.restCalls.find((c) => c.path === "/bots/slack/setup");
  expect(setup?.body).toEqual({ bot_token: "xoxb-browser-secret", app_token: "xapp-browser-secret", signing_secret: "signing-browser-secret" });
  expect(api.calls.some((c) => c.query.includes("updateSetting") && c.variables.key === "slack_bot")).toBeFalsy();
  expect(api.restCalls.some((c) => c.path === "/bots/slack/test")).toBeTruthy();
});

test("GitHub bot setup persists its signing secret and explains PR fact validation", async ({ page }) => {
  await openBotsDestination(page);
  await page.getByRole("button", { name: "Manage setup" }).nth(1).click();
  const drawer = page.getByRole("dialog", { name: "Set up GitHub bot" });
  await expect(drawer.getByText(/webhooks\/github/)).toBeVisible();
  await expect(drawer.getByText(/tick.*Pushes.*Issue comments.*Pull requests/)).toBeVisible();
  await drawer.getByRole("button", { name: "Next" }).click();
  await drawer.getByRole("button", { name: "Generate" }).click();
  await drawer.getByRole("button", { name: "Save secret" }).click();
  await expect(drawer.getByText("Saved", { exact: true })).toBeVisible();
  await expect(drawer.getByText(/@Mari validate facts/)).toBeVisible();
  await drawer.getByRole("button", { name: "Next" }).click();
  await expect(drawer.getByText(/Delivery received/)).toBeVisible();
  expect(api.calls.some((c) => c.query.includes("updateSetting") && c.variables.key === "github_bot")).toBeTruthy();
});
