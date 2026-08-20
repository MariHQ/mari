import { expect, test, type Page } from "@playwright/test";
import { installMockApi, type MockApi } from "./fixtures/mock-api";

async function expectStableSurface(page: Page, browserErrors: string[]) {
  await expect(page.locator("#main-content")).toBeVisible();
  const overflow = await page.evaluate(() => ({
    document: document.documentElement.scrollWidth - window.innerWidth,
    main: (() => {
      const main = document.querySelector<HTMLElement>("#main-content");
      return main ? main.scrollWidth - main.clientWidth : 0;
    })(),
  }));
  expect(overflow).toEqual({ document: 0, main: 0 });
  expect(browserErrors).toEqual([]);
}

function observeBrowserErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(`console: ${message.text()}`);
  });
  return errors;
}

async function openConnectStep(page: Page) {
  await page.goto("/welcome");
  await expect(page.getByRole("heading", { name: "Build your knowledge workspace" })).toBeVisible();
  await page.getByRole("button", { name: "Set up my workspace" }).click();
  await expect(page.getByRole("heading", { name: "Connect your knowledge" })).toBeVisible();
}

test("onboarding completes a connector, glossary review, and finish workflow", async ({ page }) => {
  const errors = observeBrowserErrors(page);
  const api: MockApi = await installMockApi(page);
  api.setData("glossaryCandidates", [{
    id: 31, term: "Lifecycle policy", variants: "lifecycle",
    definition: "The retention and deletion rules for records.",
    evidence: "Retention runbook", evidenceDocId: 1,
  }]);

  await page.goto("/welcome");
  await expect(page.getByText("Four steps, all of them real.")).toBeVisible();
  const steps = page.getByRole("list", { name: "Onboarding steps" });
  await expect(steps.getByRole("listitem")).toHaveCount(4);
  for (const label of ["Welcome", "Connect", "Glossary", "Finish"]) {
    await expect(steps.getByRole("button", { name: new RegExp(label) })).toBeVisible();
  }
  await expect(page.getByText(/Five steps/)).toHaveCount(0);

  await page.getByRole("button", { name: "Set up my workspace" }).click();
  await expect(page.getByRole("button", { name: /Confluence/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /^Upload/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /^Website/ })).toHaveCount(0);
  await page.getByRole("button", { name: /Confluence/ }).click();

  await expect(page.getByRole("heading", { name: "Connect Confluence" })).toBeVisible();
  await page.getByLabel("Site URL").fill("https://acme.atlassian.test");
  await page.getByLabel("Atlassian account email").fill("docs@example.test");
  await page.getByLabel("API token").fill("atl-browser-secret");
  await expect(page.getByLabel("API token")).toHaveAttribute("type", "password");
  await page.getByRole("button", { name: "Test connection" }).click();
  await expect(page.getByText(/Connection OK/)).toBeVisible();
  await page.getByRole("button", { name: "Connect & sync" }).click();
  await expect(page.getByText(/initial sync runs on the server/i)).toBeVisible();
  await page.getByRole("button", { name: /^Done/ }).click();

  await expect(page.getByRole("heading", { name: "Seed your glossary" })).toBeVisible();
  await page.getByRole("button", { name: "Scan my documents" }).click();
  await expect(page.getByText("Lifecycle policy", { exact: true })).toBeVisible();
  await expect(page.getByText("Retention runbook", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Add 1 term" }).click();
  await expect(page.getByText("Added 1 term to your glossary.")).toBeVisible();

  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("heading", { name: "Finish setup" })).toBeVisible();
  await page.getByRole("button", { name: "Finish setup" }).click();
  await expect(page).toHaveURL(/\/$/);

  expect(api.restCalls.some((call) => call.path === "/connectors/validate"
    && call.body.provider === "confluence")).toBeTruthy();
  expect(api.restCalls.some((call) => call.path === "/connectors/connect"
    && call.body.config.api_token === "atl-browser-secret")).toBeTruthy();
  expect(api.calls.some((call) => call.query.includes("harvestGlossary"))).toBeTruthy();
  expect(api.calls.some((call) => call.query.includes("promoteGlossaryCandidate")
    && call.variables.id === 31)).toBeTruthy();
  await expectStableSurface(page, errors);
});

test("connector validation failures stay actionable and do not advance onboarding", async ({ page }) => {
  const errors = observeBrowserErrors(page);
  await installMockApi(page);
  await page.route("**/connectors/validate", (route) => route.fulfill({
    status: 200, json: { ok: false, error: "Confluence rejected this API token." },
  }));
  await openConnectStep(page);
  await page.getByRole("button", { name: /Confluence/ }).click();
  await page.getByLabel("Site URL").fill("https://acme.atlassian.test");
  await page.getByLabel("Atlassian account email").fill("docs@example.test");
  await page.getByLabel("API token").fill("bad-token");
  await page.getByRole("button", { name: "Test connection" }).click();
  await expect(page.getByText("Confluence rejected this API token.")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Connect Confluence" })).toBeVisible();

  await page.getByLabel("API token").fill("replacement-token");
  await expect(page.getByText("Confluence rejected this API token.")).toHaveCount(0);
  await expectStableSurface(page, errors);
});

test("GitHub onboarding uses its repository transport and returns to the connector grid", async ({ page }) => {
  const errors = observeBrowserErrors(page);
  const api = await installMockApi(page);
  await openConnectStep(page);
  await page.getByRole("button", { name: /GitHub/ }).click();
  await expect(page.getByRole("heading", { name: "Connect GitHub" })).toBeVisible();

  await page.getByRole("button", { name: "← All connectors" }).click();
  await expect(page.getByRole("heading", { name: "Connect your knowledge" })).toBeVisible();
  await page.getByRole("button", { name: /GitHub/ }).click();
  await page.getByLabel("Personal access token").fill("github_pat_onboarding");
  await page.getByLabel("Repository").fill("acme/product-docs");
  await page.getByRole("button", { name: "Test connection" }).click();
  await expect(page.getByText(/Connection OK/)).toBeVisible();
  await page.getByRole("button", { name: "Connect & sync" }).click();
  await expect(page.getByText(/initial sync runs on the server/i)).toBeVisible();

  expect(api.calls.some((call) => call.query.includes("connectGithubRepo")
    && call.variables.repo === "acme/product-docs"
    && call.variables.token === "github_pat_onboarding")).toBeTruthy();
  await expectStableSurface(page, errors);
});
