import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { installMockApi } from "./fixtures/mock-api";

const ROUTES = [
  "/", "/tasks", "/facts", "/decisions", "/knowledge", "/knowledge/doc?id=1",
  "/answers", "/insights", "/audit", "/lineage", "/flows", "/library",
  "/publish", "/trajectories", "/sources", "/preferences", "/welcome",
  "/settings/general", "/settings/models", "/settings/design", "/settings/members",
  "/settings/api-keys", "/settings/audit",
] as const;

test.beforeEach(async ({ page }) => { await installMockApi(page); });

for (const route of ROUTES) {
  test(`${route} has no serious WCAG violations`, async ({ page }) => {
    await page.goto(route);
    await expect(page.locator("#main-content")).toBeVisible();
    const result = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    const violations = result.violations
      .filter((violation) => violation.impact === "critical" || violation.impact === "serious")
      .map((violation) => ({
        id: violation.id,
        impact: violation.impact,
        help: violation.help,
        targets: violation.nodes.slice(0, 5).map((node) => node.target.join(" ")),
      }));
    expect(violations).toEqual([]);
  });
}
