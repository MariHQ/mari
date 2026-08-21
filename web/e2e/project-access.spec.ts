import { expect, test } from "@playwright/test";
import { installMockApi } from "./fixtures/mock-api";

test("switching projects scopes subsequent browser requests", async ({ page }) => {
  const seen: string[] = [];
  page.on("request", (request) => {
    if (request.url().endsWith("/graphql")) seen.push(request.headers()["x-mari-project"] || "");
  });
  await installMockApi(page, { projects: [
    { id: 7, slug: "alpha", name: "Alpha", role: "admin", capabilities: ["knowledge.read"] },
    { id: 9, slug: "beta", name: "Beta", role: "viewer", capabilities: ["knowledge.read"] },
  ] });

  await page.goto("/");
  await page.getByRole("button", { name: /Account:/ }).click();
  await page.getByRole("menuitem", { name: "Beta" }).click();
  await page.goto("/knowledge");

  await expect.poll(() => seen.includes("beta")).toBeTruthy();
  await page.getByRole("button", { name: /Account:/ }).click();
  await expect(page.getByRole("menuitem", { name: /Beta · Current/ })).toBeVisible();
});
