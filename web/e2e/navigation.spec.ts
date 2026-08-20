import { expect, test } from "@playwright/test";
import { installMockApi } from "./fixtures/mock-api";

const ROUTES = [
  ["/", null, "Home"], ["/tasks", "Review", "Review"], ["/facts", "Facts", "Knowledge"],
  ["/decisions", "Decisions", "Knowledge"], ["/knowledge", "Knowledge", "Knowledge"],
  ["/knowledge/doc?id=1", "Retention runbook", "Knowledge"], ["/answers", "Approved answers", "Knowledge"],
  ["/insights", "Insights", "Analytics"], ["/audit", "Repository audit", "Review"], ["/lineage", "Lineage", "Knowledge"],
  ["/flows", "Automations", "Automations"], ["/library", "Library", "Knowledge"], ["/publish", "Destinations", "Destinations"],
  ["/trajectories", "Agent trajectories", "Analytics"],
  ["/sources", "Sources", "Sources"], ["/settings/general", "General", "Settings"],
  ["/settings/models", "Models", "Settings"], ["/settings/design", "Design & brand", "Settings"],
  ["/settings/members", "Members", "Settings"], ["/settings/api-keys", "API keys", "Settings"],
  ["/settings/audit", "Audit log", "Settings"], ["/preferences", "Preferences", null], ["/welcome", "Welcome", null],
] as const;

const PRIMARY_DESTINATIONS = [
  "Home", "Knowledge", "Review", "Automations", "Destinations", "Analytics", "Sources", "Settings",
] as const;

const LEGACY_PRIMARY_LABELS = [
  "Overview", "Tasks", "Answers", "Decisions", "Library", "Lineage", "Facts",
  "Repository audit", "Flows", "Publish", "Insights", "Agent trajectories",
] as const;

async function primaryNavigation(page: import("@playwright/test").Page) {
  const menu = page.getByRole("button", { name: "Menu" });
  if (await menu.isVisible().catch(() => false)) await menu.click();
  const navigation = page.getByRole("navigation", { name: "Primary" });
  await expect(navigation).toBeVisible();
  return navigation;
}

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
    const main = document.querySelector<HTMLElement>("#main-content");
    if (main && main.scrollWidth > main.clientWidth + 1) {
      const edge = main.getBoundingClientRect().right;
      const offenders = [...main.querySelectorAll<HTMLElement>("*")]
        .filter((node) => visible(node) && node.getBoundingClientRect().right > edge + 1)
        .slice(0, 4)
        .map((node) => `${node.tagName.toLowerCase()}.${node.className}`);
      issues.push(`main content overflow ${main.scrollWidth}px > ${main.clientWidth}px: ${offenders.join(" | ")}`);
    }
    return issues;
  });
  expect(violations).toEqual([]);
}

test.beforeEach(async ({ page }) => { await installMockApi(page); });

for (const [path, title, parent] of ROUTES) {
  test(`${path} renders its browser route`, async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto(path);
    if (title) await expect(page.getByText(title, { exact: true }).first()).toBeVisible();
    await expectUiContract(page);
    if (parent) {
      const navigation = await primaryNavigation(page);
      await expect(navigation.locator('[aria-current="page"]')).toHaveText(parent);
    }
    expect(errors).toEqual([]);
  });
}

test("primary navigation exposes only the consolidated information architecture", async ({ page }) => {
  await page.goto("/");
  const navigation = await primaryNavigation(page);
  for (const label of PRIMARY_DESTINATIONS) {
    await expect(navigation.getByRole("button", { name: label, exact: true })).toBeVisible();
  }
  for (const label of LEGACY_PRIMARY_LABELS) {
    await expect(navigation.getByRole("button", { name: label, exact: true })).toHaveCount(0);
  }
});

test("SPA navigation moves keyboard context to main content", async ({ page }) => {
  await page.goto("/");
  const navigation = await primaryNavigation(page);
  await navigation.getByRole("button", { name: "Knowledge", exact: true }).click();
  await expect(page).toHaveURL(/\/knowledge$/);
  await expect(page.locator("#main-content")).toBeFocused();
});

test("reduced-motion preference suppresses application animation and transitions", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");
  const durations = await page.evaluate(() => {
    const probe = document.createElement("div");
    probe.className = "animate-spin transition-all";
    document.body.appendChild(probe);
    const style = getComputedStyle(probe);
    const result = { animation: style.animationDuration, transition: style.transitionDuration };
    probe.remove();
    return result;
  });
  expect(parseFloat(durations.animation)).toBeLessThanOrEqual(0.001);
  expect(parseFloat(durations.transition)).toBeLessThanOrEqual(0.001);
});

test("unknown routes recover to the authenticated overview", async ({ page }) => {
  await page.goto("/does-not-exist");
  await expect(page).toHaveURL(/\/$/);
  const navigation = await primaryNavigation(page);
  await expect(navigation.locator('[aria-current="page"]')).toHaveText("Home");
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
  await expect(page.getByRole("button", { name: /Email me a magic link/i })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Create an account/i })).toHaveCount(0);
  await expectUiContract(page);
});

test("first-run setup is reachable only while the workspace needs setup", async ({ page }) => {
  await page.unrouteAll({ behavior: "wait" });
  const api = await installMockApi(page, { signedIn: false, needsSetup: true });
  api.setData("sourcePulse", []);
  await page.goto("/setup");
  await expect(page.getByRole("heading", { name: "Welcome to Mari" })).toBeVisible();
  await expect(page.getByText(/server logs/i)).toHaveCount(0);
  await expect(page.getByLabel("Admin token")).toHaveCount(0);
  await page.getByLabel("Your name").fill("Dana Rodriguez");
  await page.getByLabel("Email").fill("dana@example.test");
  await page.getByLabel("Password", { exact: true }).fill("correct horse battery staple");
  await page.getByLabel("Confirm password").fill("correct horse battery staple");
  await page.getByLabel("Workspace name").fill("Acme Product");
  await page.getByRole("button", { name: "Finish setup" }).click();
  await expect.poll(() => api.restCalls.find((call) => call.path === "/auth/setup")?.body).toEqual({
    name: "Dana Rodriguez",
    email: "dana@example.test",
    password: "correct horse battery staple",
    workspace: "Acme Product",
  });
  await expect(page).toHaveURL(/\/welcome$/);
  await expectUiContract(page);
});
