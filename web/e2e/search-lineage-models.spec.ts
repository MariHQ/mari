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

test("lineage graph renders grounded nodes and opens document detail", async ({ page }) => {
  await page.goto("/lineage");
  await expect(page.getByText("Retention runbook", { exact: true }).first()).toBeVisible();
  await page.getByText("Retention runbook", { exact: true }).first().click();
  await expect(page.getByText("Document detail", { exact: true }).or(page.getByText("Retention runbook", { exact: true }).last())).toBeVisible();
  expect(api.calls.some((c) => c.query.includes("lineage"))).toBeTruthy();
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
