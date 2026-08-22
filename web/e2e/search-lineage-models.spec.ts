import { expect, test } from "@playwright/test";
import { installMockApi, type MockApi } from "./fixtures/mock-api";

let api: MockApi;
test.beforeEach(async ({ page }) => { api = await installMockApi(page); });

test("knowledge search is URL-addressable and reaches hybrid retrieval", async ({ page }) => {
  await page.goto("/knowledge");
  const search = page.getByPlaceholder("Search knowledge…");
  await search.fill("retention policy");
  await expect(page).toHaveURL(/q=retention(?:\+|%20)policy/);
  await expect(page.getByText("Retention runbook", { exact: true }).first()).toBeVisible();
  await expect.poll(() => api.calls.some((c) => c.query.includes("search") && c.variables.q === "retention policy")).toBeTruthy();
});

test("knowledge exposes every loaded source without overlapping content tabs", async ({ page }) => {
  api.setData("search", [
    { id: 11, source: "confluence", title: "Confluence policy", snippet: "Canonical policy", body: "Canonical policy", kind: "page", author: "Dana", authorInitials: "DR", date: "2026-08-19", tags: ["canonical"] },
    { id: 12, source: "gdrive", title: "Drive handbook", snippet: "Team handbook", body: "Team handbook", kind: "page", author: "Lee", authorInitials: "LC", date: "2026-08-19", tags: [] },
    { id: 13, source: "custom_archive", title: "Archive note", snippet: "Legacy record", body: "Legacy record", kind: "page", author: "Sam", authorInitials: "SA", date: "2026-08-19", tags: [] },
  ]);
  api.setData("searchTotal", 3);
  await page.goto("/knowledge");

  await expect(page.getByRole("checkbox", { name: /Confluence/ })).toBeVisible();
  await expect(page.getByRole("checkbox", { name: /Google Drive/ })).toBeVisible();
  await expect(page.getByRole("checkbox", { name: /Custom Archive/ })).toBeVisible();
  await expect(page.getByRole("checkbox", { name: /Dana/ })).toBeVisible();
  await expect(page.getByRole("checkbox", { name: /Lee/ })).toBeVisible();
  await expect(page.getByRole("checkbox", { name: /Canonical/ })).toBeVisible();
  const tabs = page.getByRole("group", { name: "KnowledgeResult type" });
  await expect(tabs.getByRole("button", { name: /Documents/ })).toBeVisible();
  await expect(tabs.getByRole("button", { name: /^Pages/ })).toHaveCount(0);

  await page.getByRole("checkbox", { name: /Confluence/ }).locator("xpath=..").click();
  await expect(page.getByText("Confluence policy", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Drive handbook", { exact: true })).toBeHidden();
  await expect(page.getByText("Archive note", { exact: true })).toBeHidden();
  await page.getByRole("checkbox", { name: /Confluence/ }).locator("xpath=..").click();
  await page.getByRole("checkbox", { name: /Canonical/ }).locator("xpath=..").click();
  await expect(page.getByText("Confluence policy", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Drive handbook", { exact: true })).toBeHidden();
});

test("lineage opens with document detail and can switch to a rolled-up overview", async ({ page }) => {
  await page.goto("/lineage");
  const question = page.getByLabel("Lineage question");
  await expect(question.getByRole("button", { name: "Documents" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText(/1 documents · 0 recorded relationships/i)).toBeVisible();
  await expect(page.getByRole("group", { name: /Documents\. Use the arrow keys/ })
    .getByRole("button", { name: /Retention runbook/i })).toBeVisible();
  await question.getByRole("button", { name: "Overview" }).click();
  await expect(question.getByRole("button", { name: "Overview" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText(/1 group · 1 document, rolled up/i)).toBeVisible();
  await page.getByRole("group", { name: /Documents\. Use the arrow keys/ })
    .getByRole("button", { name: /GitHub · 1 document/i }).click();
  await expect(page.getByText("Rolled-up group", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /Retention runbook/i }).click();
  await expect(page).toHaveURL(/mode=provenance.*focal=github%3A1|focal=github%3A1.*mode=provenance/);
  await expect(question.getByRole("button", { name: "Provenance" })).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("group", { name: /Documents\. Use the arrow keys/ })
    .getByRole("button", { name: /Retention runbook/i }).click();
  await expect(page.getByText("Document detail", { exact: true }).or(page.getByText("Retention runbook", { exact: true }).last())).toBeVisible();
  expect(api.calls.some((c) => c.query.includes("lineage"))).toBeTruthy();
});

test("provenance and impact exclude symmetric context links", async ({ page }) => {
  api.setData("lineage", [
    { id: "dependent", docId: 1, title: "Customer guide", source: "docs", docKind: "page", icon: "file", x: 0.2, y: 0.5, pinned: false, date: "2026-08-19", createdDate: "2026-08-18", warn: false, owner: "Dana", tags: [], staleDays: 0, orphan: false, inbound: 0, outbound: 2, group: "", meta: "" },
    { id: "source", docId: 2, title: "Canonical policy", source: "github", docKind: "page", icon: "file", x: 0.5, y: 0.5, pinned: false, date: "2026-08-18", createdDate: "2026-08-17", warn: false, owner: "Lee", tags: [], staleDays: 1, orphan: false, inbound: 1, outbound: 0, group: "", meta: "" },
    { id: "neighbor", docId: 3, title: "Semantically nearby", source: "slack", docKind: "page", icon: "file", x: 0.8, y: 0.5, pinned: false, date: "2026-08-19", createdDate: "2026-08-18", warn: false, owner: "Sam", tags: [], staleDays: 0, orphan: false, inbound: 1, outbound: 1, group: "", meta: "" },
  ]);
  api.setData("lineageEdges", [
    { id: 1, fromId: "dependent", toId: "source", kind: "references", date: "2026-08-19", meta: null },
    { id: 2, fromId: "dependent", toId: "neighbor", kind: "similar", date: "2026-08-19", meta: { sim: 0.94 } },
    { id: 3, fromId: "neighbor", toId: "source", kind: "contradicts", date: "2026-08-19", meta: null },
    { id: 4, fromId: "neighbor", toId: "source", kind: "derived", date: "2026-08-19", meta: { derived: "llm", confidence: 0.7 } },
  ]);

  await page.goto("/lineage?mode=provenance&focal=dependent");
  await expect(page.getByRole("group", { name: /Lineage graph: 2 documents, 1 links/ })).toBeVisible();
  const documents = page.getByRole("group", { name: /Documents\. Use the arrow keys/ });
  await expect(documents.getByRole("button", { name: /Customer guide/i })).toBeVisible();
  await expect(documents.getByRole("button", { name: /Canonical policy/i })).toBeVisible();
  await expect(documents.getByRole("button", { name: /Semantically nearby/i })).toHaveCount(0);

  await page.goto("/lineage?mode=impact&focal=source");
  await expect(page.getByRole("group", { name: /Lineage graph: 2 documents, 1 links/ })).toBeVisible();
  await expect(documents.getByRole("button", { name: /Customer guide/i })).toBeVisible();
  await expect(documents.getByRole("button", { name: /Semantically nearby/i })).toHaveCount(0);
});

test("workspace lineage defaults persist and drive focused graph depth", async ({ page }) => {
  await page.goto("/settings/general");
  await page.getByLabel("Maximum visible lineage nodes").selectOption("24");
  await page.getByLabel("Lineage dependency depth").selectOption("2");
  await page.getByLabel("Minimum lineage confidence").selectOption("0.9");
  await page.getByRole("button", { name: "Save lineage defaults" }).click();
  await expect.poll(() => api.calls.some((call) =>
    call.query.includes("updateSetting") && call.variables.key === "lineage" &&
    (call.variables.value as any).max_nodes === 24 && (call.variables.value as any).hop_depth === 2 &&
    (call.variables.value as any).min_confidence === 0.9,
  )).toBeTruthy();

  await page.reload();
  await expect(page.getByLabel("Maximum visible lineage nodes")).toHaveValue("24");
  await expect(page.getByLabel("Lineage dependency depth")).toHaveValue("2");
  await expect(page.getByLabel("Minimum lineage confidence")).toHaveValue("0.9");
  await page.goto("/lineage?mode=provenance&focal=github%3A1");
  await expect(page.getByText(/Showing 2 dependency hops/i)).toBeVisible();
  await expect(page.getByText(/below 90% confidence/i)).toBeVisible();
});

test("Ollama embedding and generation settings save without cloud keys", async ({ page }) => {
  await page.goto("/settings/models");
  await expect(page.getByRole("option", { name: /Ollama: nomic-embed-text/ })).toHaveCount(1);
  const embedding = page.getByText("Embedding model", { exact: true }).locator("xpath=ancestor::*[contains(@class,'rounded')][1]");
  const llm = page.getByText("LLM provider", { exact: true }).locator("xpath=ancestor::*[contains(@class,'rounded')][1]");
  await embedding.getByRole("combobox").selectOption("ollama:mxbai-embed-large");
  await page.getByRole("button", { name: "Save", exact: true }).first().click();
  await embedding.getByRole("button", { name: "Re-index everything?" }).click();
  await page.getByRole("button", { name: "Save", exact: true }).nth(1).click();
  await expect.poll(() => api.calls.filter((c) => c.query.includes("updateSetting")).length).toBeGreaterThanOrEqual(2);
  expect(api.calls.some((c) => c.variables.key === "embedding" && (c.variables.value as any).provider === "ollama")).toBeTruthy();
  expect(api.calls.some((c) => c.variables.key === "llm" && (c.variables.value as any).provider === "ollama")).toBeTruthy();
});

test("provider keys use explicit edits, including legitimate bullet characters", async ({ page }) => {
  await page.goto("/settings/models");
  await page.getByLabel("OpenAI (sk-…)").fill("sk-live-•-valid");
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect.poll(() => api.calls.some((call) => call.query.includes("updateSetting") &&
    call.variables.key === "llm" && (call.variables.value as any).keys?.openai === "sk-live-•-valid")).toBeTruthy();
});

test("generation gateway validates, preserves HTTP embeddings, and runs prompt-free health", async ({ page }) => {
  await page.goto("/settings/models");
  await expect(page.getByText("Generation gateway", { exact: true })).toBeVisible();
  await expect(page.getByText(/Embeddings remain independently configured/)).toBeVisible();
  await expect(page.getByText(/Claude plugin/i)).toHaveCount(0);
  await expect(page.getByRole("option", { name: /Enterprise gateway/ })).toHaveCount(1);
  await expect(page.getByRole("option", { name: /OpenAI.*text-embedding-3-small/ })).toHaveCount(1);
  await expect(page.getByRole("option", { name: /Sentence Transformers/ })).toHaveCount(0);
  const embedding = page.getByText("Embedding model", { exact: true }).locator("xpath=ancestor::*[contains(@class,'rounded')][1]");
  await embedding.getByRole("combobox").selectOption("openai:text-embedding-3-small");
  await page.getByRole("button", { name: "Save", exact: true }).first().click();
  await embedding.getByRole("button", { name: "Re-index everything?" }).click();
  await expect.poll(() => api.calls.filter((call) => call.variables.key === "embedding").length).toBe(1);

  await page.getByLabel("Generation model").fill("rippling-chat");
  await page.getByLabel("Routing headers (JSON)").fill("not-json");
  await page.getByRole("button", { name: "Save gateway" }).click();
  await expect(page.getByText("Routing headers must be valid JSON.")).toBeVisible();
  expect(api.calls.filter((call) => call.query.includes("updateSetting"))).toHaveLength(1);

  await page.getByLabel("Gateway base URL").fill("https://corp-gateway.example/v1");
  await page.getByLabel("API compatibility").selectOption("deepseek");
  await page.getByLabel("Generation model").fill("deepseek-v4-flash");
  await page.getByLabel("Routing headers (JSON)").fill('{"X-Tenant":"rippling"}');
  await page.getByLabel("Request metadata (JSON)").fill('{"application":"mari-browser"}');
  await page.getByLabel("Model routing header").fill("X-Model-ID");
  await page.getByLabel("Retry count").fill("3");
  await page.getByRole("button", { name: "Save gateway" }).click();
  await expect.poll(() => api.calls.filter((call) => call.query.includes("updateSetting")).length).toBe(2);
  const llmSave = api.calls.find((call) => call.query.includes("updateSetting") && call.variables.key === "llm");
  expect((llmSave?.variables.value as any).provider).toBe("gateway");
  expect((llmSave?.variables.value as any).model).toBe("deepseek-v4-flash");
  expect((llmSave?.variables.value as any).gateway.compatibility).toBe("deepseek");
  expect((llmSave?.variables.value as any).gateway.token).toBe("••••…oken");
  expect((llmSave?.variables.value as any).gateway.headers).toEqual({ "X-Tenant": "rippling" });
  const embeddingSaves = api.calls.filter((call) => call.variables.key === "embedding");
  expect(embeddingSaves).toHaveLength(1);
  expect((embeddingSaves[0].variables.value as any).provider).toBe("openai");

  await page.getByRole("button", { name: "Test gateway" }).click();
  await expect(page.getByText("Gateway healthy", { exact: true })).toBeVisible();
  await expect(page.getByText("LLM gateway is reachable and authenticated", { exact: true })).toBeVisible();
  expect(api.calls.some((call) => call.query.includes("testLlmGateway"))).toBeTruthy();
  await page.reload();
  await expect(page.getByRole("textbox", { name: "Gateway token" })).toHaveValue("••••…oken");
});

test("generation gateway form remains usable on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/settings/models");
  await expect(page.getByText("Generation gateway", { exact: true })).toBeVisible();
  await page.getByLabel("Gateway base URL").fill("https://mobile-gateway.example/v1");
  await page.getByLabel("Generation model").fill("mobile-chat");
  await page.getByRole("button", { name: "Save gateway" }).click();
  await expect.poll(() => api.calls.some((call) => call.variables.key === "llm" && (call.variables.value as any).model === "mobile-chat")).toBeTruthy();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
});
