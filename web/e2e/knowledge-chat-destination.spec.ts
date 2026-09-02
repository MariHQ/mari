import { expect, test } from "@playwright/test";
import { installMockApi, type MockApi } from "./fixtures/mock-api";

let api: MockApi;
test.beforeEach(async ({ page }) => { api = await installMockApi(page); });

test("admin creates, configures, and deploys a knowledge chat destination", async ({ page }) => {
  await page.goto("/publish?tab=chat");
  await expect(page.getByRole("button", { name: "Knowledge chat", exact: true })).toHaveAttribute("aria-pressed", "true");
  await page.getByLabel("Destination name").fill("Company knowledge");
  await page.getByLabel("URL slug").fill("company-knowledge");
  await page.getByLabel("Assistant title").fill("Ask Acme");
  await page.getByLabel("Welcome message").fill("Ask about company policy.");
  await page.getByRole("button", { name: "Create knowledge chat" }).click();
  await expect(page).toHaveURL(/tab=chat&chat=7/);
  expect(api.calls.some((call) => call.query.includes("createKnowledgeChatDestination") && call.variables.slug === "company-knowledge")).toBeTruthy();

  await page.getByLabel("Welcome message").fill("Ask about trusted company policy.");
  await page.getByRole("button", { name: "Save configuration" }).click();
  expect(api.calls.some((call) => call.query.includes("updateKnowledgeChatDestination") && call.variables.welcome === "Ask about trusted company policy.")).toBeTruthy();
  await page.getByRole("button", { name: "Deploy", exact: true }).click();
  expect(api.calls.some((call) => call.query.includes("deployKnowledgeChatDestination") && call.variables.id === 7)).toBeTruthy();
  await expect(page.getByRole("button", { name: "Redeploy", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Open knowledge chat" })).toHaveAttribute(
    "href", "/knowledge-chat/default/company-knowledge",
  );
});

test("deployed knowledge chat streams an answer and cited sources", async ({ page }) => {
  await page.route("**/auth/me", (route) => route.fulfill({ json: {
    user: null, needsSetup: false, bypassEnabled: false, registrationEnabled: false,
    oauth: { github: true, google: true }, projects: [], activeProject: null, capabilities: [],
  } }));
  await page.goto("/knowledge-chat/default/company-knowledge");
  await expect(page).toHaveURL(/\/knowledge-chat\/default\/company-knowledge$/);
  await expect(page.getByRole("heading", { name: "Ask Acme" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Open the Mari agent" })).toHaveCount(0);
  await page.getByLabel("Ask a question").fill("How long is retention?");
  await page.getByRole("button", { name: "Ask" }).click();
  await expect(page.getByText("Retention is 30 days", { exact: false })).toBeVisible();
  await expect(page.getByRole("list", { name: "Sources" })).toContainText("Retention runbook");
  expect(api.restCalls.some((call) => call.path === "/knowledge-chat-api/default/company-knowledge/chat" && call.body.message === "How long is retention?")).toBeTruthy();
  const sources = page.getByRole("list", { name: "Sources" });
  await expect(sources.getByRole("link", { name: /Retention runbook/ })).toHaveAttribute("href", "https://github.com/acme/runbooks/blob/main/retention.md");
  await expect(sources.locator('a[href^="/knowledge/doc"]')).toHaveCount(0);
});

test("knowledge chat admin and end-user surfaces remain usable on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  api.setData("knowledgeChatDestinations", [{
    id: 8, name: "Mobile knowledge", slug: "mobile-knowledge", title: "Ask on mobile",
    welcome: "Ask a mobile question.", status: "draft", url: "/knowledge-chat/default/mobile-knowledge",
  }]);
  await page.goto("/publish?tab=chat&chat=8");
  await expect(page.getByLabel("Destination name")).toBeVisible();
  await page.getByRole("button", { name: "Deploy", exact: true }).click();
  await expect(page.getByRole("button", { name: "Redeploy", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Open knowledge chat" })).toHaveAttribute(
    "href", "/knowledge-chat/default/mobile-knowledge",
  );
  await page.goto("/knowledge-chat/default/company-knowledge");
  await expect(page.getByLabel("Ask a question")).toBeVisible();
  await expect(page.locator("body")).not.toHaveCSS("overflow-x", "scroll");
});

const source = (n: number, title: string) => JSON.stringify({
  n, source: "confluence", kind: "page", title, snippet: `${title} snippet`, meta: `${title} snippet`,
  author: "Prabhat Sharma", updated: "2026-08-30T00:00:00Z", tags: [], document_id: n,
  href: `/knowledge/doc?id=${n}`, source_url: `https://wiki.example.com/pages/${n}`, score: 1,
});

/** A stream in the server's shape: every retrieved page in meta, the answer,
    then the settled rail in a sources event. Registered after the fixture's
    route, so it wins for this test. */
async function streamAnswer(page: import("@playwright/test").Page, answer: string, cited: string[]) {
  await page.route("**/auth/me", (route) => route.fulfill({ json: {
    user: null, needsSetup: false, bypassEnabled: false, registrationEnabled: false,
    oauth: { github: true, google: true }, projects: [], activeProject: null, capabilities: [],
  } }));
  await page.route("**/knowledge-chat-api/*/*/chat", (route) => route.fulfill({ status: 200, contentType: "text/event-stream", body:
    `event: meta\ndata: {"session_id":"41.tok","sources":[${source(1, "Playbook: Reverting a MongoDB migration")},${source(2, "Kubernetes cluster runbook")}]}\n\n` +
    `event: token\ndata: ${JSON.stringify({ token: answer })}\n\n` +
    `event: sources\ndata: {"sources":[${cited.join(",")}]}\n\nevent: done\ndata: {"session_id":"41.tok"}\n\n` }));
}

test("a not-found answer shows no sources and reads as plain prose", async ({ page }) => {
  await streamAnswer(page, "I could not find this in the connected sources.", []);
  await page.goto("/knowledge-chat/default/company-knowledge");
  await page.getByLabel("Ask a question").fill("how do k8s clusters work here?");
  await page.getByRole("button", { name: "Ask" }).click();
  const answer = page.getByText("I could not find this in the connected sources.");
  await expect(answer).toBeVisible();
  // Not a code box: the sentence lives in a paragraph, not in <pre> or <code>.
  await expect(page.locator("pre, code").filter({ hasText: "could not find" })).toHaveCount(0);
  await expect(page.getByRole("list", { name: "Sources" })).toHaveCount(0);
  await expect(page.getByText("MongoDB")).toHaveCount(0);
});

test("the sources rail shows only the pages the answer cites", async ({ page }) => {
  await streamAnswer(page, "Clusters are provisioned per team [2].", [source(2, "Kubernetes cluster runbook")]);
  await page.goto("/knowledge-chat/default/company-knowledge");
  await page.getByLabel("Ask a question").fill("how do k8s clusters work here?");
  await page.getByRole("button", { name: "Ask" }).click();
  await expect(page.getByText("Clusters are provisioned per team", { exact: false })).toBeVisible();
  const sources = page.getByRole("list", { name: "Sources" });
  await expect(sources).toContainText("Kubernetes cluster runbook");
  await expect(sources).not.toContainText("MongoDB");
  await expect(sources.getByRole("listitem")).toHaveCount(1);
});
