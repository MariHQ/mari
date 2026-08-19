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

test("lineage opens as a rolled-up overview and drills into focused provenance", async ({ page }) => {
  await page.goto("/lineage");
  const question = page.getByLabel("Lineage question");
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
  await embedding.getByRole("button", { name: "Save" }).click();
  await embedding.getByRole("button", { name: "Re-index everything?" }).click();
  await llm.getByRole("button", { name: "Save" }).click();
  await expect.poll(() => api.calls.filter((c) => c.query.includes("updateSetting")).length).toBeGreaterThanOrEqual(2);
  expect(api.calls.some((c) => c.variables.key === "embedding" && (c.variables.value as any).provider === "ollama")).toBeTruthy();
  expect(api.calls.some((c) => c.variables.key === "llm" && (c.variables.value as any).provider === "ollama")).toBeTruthy();
});
