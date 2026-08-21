import { expect, test, type Page } from "@playwright/test";

const PRODUCTION_ROUTES = [
  ["/", null], ["/tasks", "Review"], ["/facts", "Facts"],
  ["/decisions", "Decisions"], ["/knowledge", "Knowledge"],
  ["/knowledge/doc?id=1", "Retention runbook"], ["/answers", "Approved answers"],
  ["/insights", "Insights"], ["/audit", "Repository audit"], ["/lineage", "Lineage"],
  ["/flows", "Automations"], ["/library", "Library"], ["/publish", "Destinations"],
  ["/workflows", "Workflows"], ["/sources", "Sources"],
  ["/settings/general", "General"], ["/settings/models", "Models"],
  ["/settings/design", "Design & brand"], ["/settings/members", "Members"],
  ["/settings/api-keys", "API keys"], ["/settings/audit", "Audit log"],
  ["/preferences", "Preferences"], ["/welcome", "Welcome"],
] as const;

async function signIn(page: Page) {
  await page.goto("/login");
  const bypass = page.getByRole("button", { name: /Continue as workspace admin/ });
  await expect(bypass).toBeVisible();
  await bypass.click();
  await expect(page).toHaveURL(/\/$/);
}

test.beforeEach(async ({ page }) => { await signIn(page); });

test("every shipped route renders against production services without runtime failures", async ({ page }) => {
  const failures: string[] = [];
  page.on("pageerror", (error) => failures.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") failures.push(`console: ${message.text()}`);
  });
  page.on("requestfailed", (request) => {
    failures.push(`request: ${request.method()} ${request.url()} ${request.failure()?.errorText ?? "failed"}`);
  });
  page.on("response", (response) => {
    if (response.status() >= 500) failures.push(`response: ${response.status()} ${response.url()}`);
  });

  for (const [path, heading] of PRODUCTION_ROUTES) {
    failures.length = 0;
    await page.goto(path);
    await expect(page.locator("#main-content")).toBeVisible();
    if (heading) await expect(page.getByText(heading, { exact: true }).first()).toBeVisible();
    expect(failures, `runtime failures while rendering ${path}`).toEqual([]);
  }
});

test("assembled stack serves health and hardened web responses", async ({ page }) => {
  const health = await page.request.get("/readyz");
  expect(health.ok()).toBeTruthy();
  const body = await health.json();
  expect(body.ok).toBe(true);
  expect(body.dependencies.database).toBe("ok");

  const response = await page.request.get("/");
  expect(response.headers()["x-content-type-options"]).toBe("nosniff");
  expect(response.headers()["x-frame-options"]).toBe("DENY");
  expect(response.headers()["content-security-policy"]).toContain("default-src 'self'");
  expect((await page.request.get("/metrics")).status()).toBe(404);
});

test("real Postgres knowledge is searchable through the production web image", async ({ page }) => {
  await page.goto("/knowledge?q=retention");
  await expect(page.getByText("Retention runbook", { exact: true }).first()).toBeVisible();
  await expect(page.getByText(/retained for 30 days/i).first()).toBeVisible();
});

test("review writes persist through GraphQL and a browser reload", async ({ page }) => {
  await page.goto("/tasks");
  const title = `CI persisted review ${Date.now()}`;
  await page.getByRole("textbox", { name: "Review item", exact: true }).fill(title);
  await page.getByRole("button", { name: "Add review item" }).click();
  await expect(page.getByText(title, { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByText(title, { exact: true })).toBeVisible();
});

test("project identity and audit data survive a new browser context", async ({ browser }) => {
  const context = await browser.newContext();
  const second = await context.newPage();
  await signIn(second);
  await second.goto("/settings/audit");
  await expect(second.getByRole("heading", { name: "Access log" })).toBeVisible();
  await context.close();
});

test("agent streams a complete turn through the real model boundary", async ({ page }) => {
  test.setTimeout(120_000);
  await page.goto("/");
  await page.getByRole("button", { name: "Open the Mari agent" }).click();
  await page.getByPlaceholder("Ask Mari…").fill("What can you help me do in this workspace?");
  await page.getByRole("button", { name: /Send/ }).click();
  const dock = page.getByRole("complementary", { name: "Mari agent" });
  await expect(dock.locator("div.flex.flex-col.gap-1").last()).not.toBeEmpty({ timeout: 90_000 });
  await expect(dock.getByRole("button", { name: "Stop" })).toHaveCount(0);
  await expect(dock).not.toContainText("I can't reach the Mari API");
  await expect(dock).not.toContainText("Agent execution stopped");
});

test("knowledge chat is created, deployed, and answers from real indexed knowledge", async ({ page }) => {
  test.setTimeout(120_000);
  const suffix = Date.now().toString(36);
  const slug = `ci-knowledge-${suffix}`;
  await page.goto("/publish?tab=chat");
  const destinationName = page.getByLabel("Destination name");
  const newDestination = page.getByRole("button", { name: "New knowledge chat" });
  await expect(destinationName.or(newDestination)).toBeVisible();
  if (await newDestination.isVisible()) await newDestination.click();
  await destinationName.fill(`CI knowledge ${suffix}`);
  await page.getByLabel("URL slug").fill(slug);
  await page.getByLabel("Assistant title").fill("Ask CI knowledge");
  await page.getByLabel("Welcome message").fill("Ask about verified product knowledge.");
  await page.getByRole("button", { name: "Create knowledge chat" }).click();
  await expect(page).toHaveURL(/\/publish\?tab=chat&chat=\d+$/);
  const deployed = page.waitForResponse((response) =>
    response.url().endsWith("/graphql")
      && response.request().method() === "POST"
      && (response.request().postData() ?? "").includes("deployKnowledgeChatDestination"),
  );
  await page.getByRole("button", { name: "Deploy", exact: true }).click();
  const deployResponse = await deployed;
  expect(deployResponse.ok()).toBe(true);
  const deployBody = await deployResponse.json() as {
    data?: { deployKnowledgeChatDestination?: string };
    errors?: { message?: string }[];
  };
  expect(deployBody.errors, deployBody.errors?.[0]?.message).toBeUndefined();
  expect(deployBody.data?.deployKnowledgeChatDestination).toBe(`/knowledge-chat/default/${slug}`);

  await page.goto(`/knowledge-chat/default/${slug}`);
  await expect(page.getByRole("heading", { name: "Ask CI knowledge" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Open the Mari agent" })).toHaveCount(0);
  await page.getByLabel("Ask a question").fill("How long are customer records retained?");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await expect(page.getByRole("list", { name: "Sources" })).toContainText(
    "Retention runbook", { timeout: 60_000 },
  );
  await expect(page.getByRole("button", { name: "Answering…" })).toHaveCount(0, { timeout: 90_000 });
  await expect(page.locator("article").last().locator("div").first()).not.toBeEmpty();
  await page.getByRole("link", { name: /Retention runbook/ }).click();
  await expect(page).toHaveURL(/\/knowledge\/doc\?id=1$/);
});
