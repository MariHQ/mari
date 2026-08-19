import { expect, test } from "@playwright/test";
import { installMockApi } from "./fixtures/mock-api";

const ROUTES = [
  ["/", "Overview"], ["/tasks", "Tasks"], ["/facts", "Facts"],
  ["/decisions", "Decisions"], ["/knowledge", "Knowledge"],
  ["/knowledge/doc?id=1", "Retention runbook"], ["/answers", "Approved answers"],
  ["/insights", "Insights"], ["/audit", "Repository audit"], ["/lineage", "Lineage"],
  ["/flows", "Flows"], ["/library", "Library"], ["/publish", "Publish"],
  ["/trajectories", "Agent trajectories"],
  ["/sources", "Sources"], ["/settings/general", "General"],
  ["/settings/models", "Models"], ["/settings/design", "Design & brand"],
  ["/settings/members", "Members"], ["/settings/api-keys", "API keys"],
  ["/settings/audit", "Audit log"], ["/preferences", "Preferences"], ["/welcome", "Welcome"],
] as const;

async function expectUiContract(page: import("@playwright/test").Page) {
  await expect(page.locator("#main-content")).toHaveCount(1);
  await expect(page.locator("#main-content")).toBeVisible();

  const violations = await page.evaluate(() => {
    const visible = (node: HTMLElement) => Boolean(node.offsetWidth || node.offsetHeight || node.getClientRects().length);
    const issues: string[] = [];
    const ids = [...document.querySelectorAll<HTMLElement>("[id]")].map((node) => node.id);
    const duplicates = [...new Set(ids.filter((id, index) => id && ids.indexOf(id) !== index))];
    if (duplicates.length) issues.push(`duplicate ids: ${duplicates.join(", ")}`);

    for (const button of document.querySelectorAll<HTMLButtonElement>("button")) {
      if (!visible(button)) continue;
      const name = button.getAttribute("aria-label") || button.getAttribute("title") || button.textContent || "";
      if (!name.trim()) issues.push(`unnamed button: ${button.outerHTML.slice(0, 120)}`);
    }
    for (const control of document.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>("input, select, textarea")) {
      if (!visible(control) || control.type === "hidden") continue;
      const named = control.labels?.length || control.getAttribute("aria-label") || control.getAttribute("aria-labelledby") || control.getAttribute("title");
      if (!named) issues.push(`unnamed control: ${control.outerHTML.slice(0, 120)}`);
    }
    if (document.documentElement.scrollWidth > window.innerWidth + 1) {
      const offenders = [...document.querySelectorAll<HTMLElement>("body *")]
        .filter((node) => visible(node) && node.getBoundingClientRect().right > window.innerWidth + 1)
        .slice(0, 4).map((node) => `${node.tagName.toLowerCase()}.${node.className}`);
      issues.push(`horizontal overflow ${document.documentElement.scrollWidth}px > ${window.innerWidth}px: ${offenders.join(" | ")}`);
    }
    return issues;
  });
  expect(violations).toEqual([]);
}

test.beforeEach(async ({ page }) => { await installMockApi(page); });

for (const [path, title] of ROUTES) {
  test(`${path} renders its browser route`, async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto(path);
    await expect(page.getByText(title, { exact: true }).first()).toBeVisible();
    await expectUiContract(page);
    expect(errors).toEqual([]);
  });
}

test("unknown routes recover to the authenticated overview", async ({ page }) => {
  await page.goto("/does-not-exist");
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByText("Overview", { exact: true }).first()).toBeVisible();
});

test("signed-out protected routes redirect to login", async ({ page }) => {
  await page.unrouteAll({ behavior: "wait" });
  await installMockApi(page, { signedIn: false });
  await page.goto("/facts");
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByText("Sign in", { exact: true }).first()).toBeVisible();
});

test("the login route is a labelled, overflow-safe browser surface", async ({ page }) => {
  await page.unrouteAll({ behavior: "wait" });
  await installMockApi(page, { signedIn: false });
  await page.goto("/login");
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await expectUiContract(page);
});

test("first-run setup is reachable only while the workspace needs setup", async ({ page }) => {
  await page.unrouteAll({ behavior: "wait" });
  await installMockApi(page, { signedIn: false, needsSetup: true });
  await page.goto("/setup");
  await expect(page.getByRole("heading", { name: "Welcome to Mari" })).toBeVisible();
  await expectUiContract(page);
});
