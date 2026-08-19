import { expect, test, type Page } from "@playwright/test";

const env = process.env;
const mutations = env.MARI_E2E_MUTATIONS === "1";

async function signIn(page: Page) {
  await page.goto("/sources");
  if (!page.url().endsWith("/login")) return;
  if (env.MARI_E2E_EMAIL && env.MARI_E2E_PASSWORD) {
    await page.getByLabel("Email").fill(env.MARI_E2E_EMAIL);
    await page.getByLabel("Password").fill(env.MARI_E2E_PASSWORD);
    await page.getByRole("button", { name: /^Sign in/ }).click();
  } else if (await page.getByRole("button", { name: /Continue as workspace admin/ }).isVisible().catch(() => false)) {
    await page.getByRole("button", { name: /Continue as workspace admin/ }).click();
  } else {
    throw new Error("Live app requires MARI_E2E_EMAIL and MARI_E2E_PASSWORD (or auth bypass).");
  }
  await expect(page).not.toHaveURL(/\/login$/);
}

const connectors = [
  {
    name: "Confluence",
    required: ["MARI_E2E_CONFLUENCE_SITE_URL", "MARI_E2E_CONFLUENCE_EMAIL", "MARI_E2E_CONFLUENCE_API_TOKEN"],
    fields: { "Site URL": "MARI_E2E_CONFLUENCE_SITE_URL", "Atlassian account email": "MARI_E2E_CONFLUENCE_EMAIL", "API token": "MARI_E2E_CONFLUENCE_API_TOKEN" },
  },
  {
    name: "Slack",
    required: ["MARI_E2E_SLACK_BOT_TOKEN"],
    fields: { "Bot token": "MARI_E2E_SLACK_BOT_TOKEN", "Channels": "MARI_E2E_SLACK_CHANNELS" },
  },
  {
    name: "Google Drive",
    required: ["MARI_E2E_GDRIVE_ACCESS_TOKEN"],
    fields: { "OAuth2 access token": "MARI_E2E_GDRIVE_ACCESS_TOKEN", "Folder ID": "MARI_E2E_GDRIVE_FOLDER_ID" },
  },
  {
    name: "GitHub",
    required: ["MARI_E2E_GITHUB_TOKEN", "MARI_E2E_GITHUB_REPO"],
    fields: { "Personal access token": "MARI_E2E_GITHUB_TOKEN", "Repository": "MARI_E2E_GITHUB_REPO" },
  },
] as const;

for (const connector of connectors) {
  test(`LIVE ${connector.name} validates and schedules its initial poll`, async ({ page }) => {
    test.skip(!mutations, "Set MARI_E2E_MUTATIONS=1 to allow sandbox connector creation.");
    test.skip(connector.required.some((key) => !env[key]), `Missing ${connector.required.join(", ")}`);
    await signIn(page);
    await page.getByRole("button", { name: "Add source" }).click();
    const dialog = page.getByRole("dialog", { name: /Connect/ });
    await dialog.getByRole("button", { name: new RegExp(connector.name) }).click();
    await dialog.getByRole("button", { name: "Next" }).click();
    for (const [label, key] of Object.entries(connector.fields)) {
      const value = env[key];
      if (value) await dialog.getByLabel(label).fill(value);
    }
    await dialog.getByRole("button", { name: "Test connection" }).click();
    await expect(dialog.getByText(/Connection OK/)).toBeVisible({ timeout: 30_000 });
    await dialog.getByRole("button", { name: "Connect & sync" }).click();
    await expect(dialog.getByText(/initial sync runs on the server/i)).toBeVisible({ timeout: 30_000 });
  });
}

test("LIVE Slack bot token reaches Slack auth.test", async ({ page }) => {
  test.skip(!mutations, "Set MARI_E2E_MUTATIONS=1 to allow sandbox bot configuration.");
  test.skip(!env.MARI_E2E_SLACK_BOT_TOKEN || !env.MARI_E2E_SLACK_SIGNING_SECRET, "Missing Slack bot credentials.");
  await signIn(page);
  await page.goto("/publish?tab=bots");
  await expect(page.getByRole("button", { name: "Bots", exact: true })).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "Manage setup" }).first().click();
  const drawer = page.getByRole("dialog", { name: "Set up Slack bot" });
  await drawer.getByRole("button", { name: "Next" }).click();
  await drawer.getByRole("textbox", { name: "Bot token" }).fill(env.MARI_E2E_SLACK_BOT_TOKEN!);
  await drawer.getByRole("textbox", { name: "Signing secret" }).fill(env.MARI_E2E_SLACK_SIGNING_SECRET!);
  await drawer.getByRole("button", { name: "Save credentials" }).click();
  await drawer.getByRole("button", { name: "Next" }).click();
  await drawer.getByRole("button", { name: "Test connection" }).click();
  await expect(drawer.getByText(/Connected in/)).toBeVisible({ timeout: 30_000 });
});

test("LIVE Ollama-backed fact scan completes through the browser", async ({ page }) => {
  test.skip(!mutations, "Set MARI_E2E_MUTATIONS=1 to allow a workflow run.");
  await signIn(page);
  await page.goto("/facts");
  await page.getByRole("button", { name: "Scan for facts" }).click();
  await expect(page.getByText(/SUCCEEDED|FAILED/)).toBeVisible({ timeout: 120_000 });
  await expect(page.getByText("FAILED", { exact: true })).toHaveCount(0);
});

test("LIVE MCP endpoint passes its browser health check", async ({ page }) => {
  await signIn(page);
  await page.goto("/publish?tab=mcp");
  const testButton = page.getByRole("button", { name: "Test", exact: true }).first();
  test.skip(await testButton.count() === 0, "No MCP server is configured in this workspace.");
  await testButton.click();
  await expect(page.getByText(/^Connected/)).toBeVisible({ timeout: 30_000 });
});
