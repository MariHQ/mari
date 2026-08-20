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
    fields: {
      "OAuth2 access token": "MARI_E2E_GDRIVE_ACCESS_TOKEN",
      "OAuth2 refresh token (optional)": "MARI_E2E_GDRIVE_REFRESH_TOKEN",
      "OAuth client ID (for refresh)": "MARI_E2E_GDRIVE_CLIENT_ID",
      "OAuth client secret (for refresh)": "MARI_E2E_GDRIVE_CLIENT_SECRET",
      "Folder ID (optional)": "MARI_E2E_GDRIVE_FOLDER_ID",
    },
  },
  {
    name: "GitHub",
    required: ["MARI_E2E_GITHUB_TOKEN", "MARI_E2E_GITHUB_REPO"],
    fields: { "Personal access token": "MARI_E2E_GITHUB_TOKEN", "Repository": "MARI_E2E_GITHUB_REPO" },
  },
] as const;

async function configureConnector(page: Page, connector: typeof connectors[number]) {
  await signIn(page);
  await page.getByRole("button", { name: "Add source" }).click();
  const dialog = page.getByRole("dialog", { name: /Connect/ });
  await dialog.getByRole("button", { name: new RegExp(connector.name) }).click();
  await dialog.getByRole("button", { name: "Next" }).click();
  for (const [label, key] of Object.entries(connector.fields)) {
    const value = env[key];
    if (value) await dialog.getByLabel(label).fill(value);
  }
  return dialog;
}

async function waitForCompletedSync(page: Page, sourceId: number, connectorName: string) {
  type SyncResult = { state: string; phase: string; lastError: string; docCount: number; chunkCount: number };
  let latest: SyncResult | undefined;
  await expect.poll(async () => {
    const response = await page.request.post("/graphql", {
      headers: { "Content-Type": "application/json" },
      data: {
        query: `query($id: Int!) {
          syncStatus(sourceId: $id) { state phase lastError docCount chunkCount }
        }`,
        variables: { id: sourceId },
      },
    });
    expect(response.ok(), `${connectorName} sync status request`).toBeTruthy();
    const body = await response.json();
    expect(body.errors, `${connectorName} sync status GraphQL errors`).toBeUndefined();
    latest = body.data?.syncStatus;
    if (latest?.state === "error") {
      throw new Error(`${connectorName} initial sync failed: ${latest.lastError || "unknown error"}`);
    }
    return latest?.state === "idle" && latest?.phase === "done";
  }, {
    message: `${connectorName} initial poll should reach its durable completed state`,
    timeout: 300_000,
    intervals: [500, 1_000, 2_000, 5_000],
  }).toBe(true);
  expect(latest?.docCount, `${connectorName} sandbox must contain at least one readable document`).toBeGreaterThan(0);
  expect(latest?.chunkCount, `${connectorName} documents must reach searchable chunks`).toBeGreaterThan(0);
  expect(latest?.lastError).toBe("");
}

for (const connector of connectors) {
  test(`LIVE ${connector.name} validates credentials without storing them`, async ({ page }) => {
    test.skip(connector.required.some((key) => !env[key]), `Missing ${connector.required.join(", ")}`);
    const dialog = await configureConnector(page, connector);
    await dialog.getByRole("button", { name: "Test connection" }).click();
    await expect(dialog.getByText(/Connection OK/)).toBeVisible({ timeout: 30_000 });
    await dialog.getByRole("button", { name: "Close" }).click();
    await expect(dialog).toHaveCount(0);
  });
}

for (const connector of connectors) {
  test(`LIVE ${connector.name} validates, ingests, and completes its initial poll`, async ({ page }) => {
    test.skip(!mutations, "Set MARI_E2E_MUTATIONS=1 to allow sandbox connector creation.");
    test.skip(connector.required.some((key) => !env[key]), `Missing ${connector.required.join(", ")}`);
    const dialog = await configureConnector(page, connector);
    await dialog.getByRole("button", { name: "Test connection" }).click();
    await expect(dialog.getByText(/Connection OK/)).toBeVisible({ timeout: 30_000 });
    const accepted = page.waitForResponse((response) => {
      if (response.request().method() !== "POST") return false;
      if (connector.name === "GitHub") {
        return response.url().endsWith("/graphql")
          && (response.request().postData() ?? "").includes("connectGithubRepo");
      }
      return response.url().endsWith("/connectors/connect");
    });
    await dialog.getByRole("button", { name: "Connect & sync" }).click();
    const response = await accepted;
    expect(response.ok(), `${connector.name} connection request`).toBeTruthy();
    const payload = await response.json();
    const sourceId = connector.name === "GitHub"
      ? Number(payload.data?.connectGithubRepo)
      : Number(payload.sourceId);
    expect(sourceId, `${connector.name} connection must return its source id`).toBeGreaterThan(0);
    await expect(dialog.getByText(/initial sync runs on the server/i)).toBeVisible({ timeout: 30_000 });
    await waitForCompletedSync(page, sourceId, connector.name);
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
