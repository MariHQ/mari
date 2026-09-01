import { expect, test } from "@playwright/test";
import { installMockApi, type MockApi } from "./fixtures/mock-api";

let api: MockApi;
test.beforeEach(async ({ page }) => { api = await installMockApi(page); });

test("trajectory view progressively discloses grounded layers and chronological evidence", async ({ page }) => {
  await page.goto("/workflows");
  await expect(page.getByText("Repair policy documentation", { exact: true })).toBeVisible();
  await expect(page.getByText("Updated a policy document from retrieved evidence.", { exact: true })).toBeVisible();
  await expect(page.getByText("Searched the knowledge base, inspected the runbook, and updated the document.", { exact: true })).toBeHidden();
  await page.getByRole("button", { name: "Inspect run" }).first().click();
  await expect(page.getByText("Searched the knowledge base, inspected the runbook, and updated the document.", { exact: true })).toBeVisible();
  await expect(page.getByRole("list", { name: "Trajectory steps" }).getByRole("listitem")).toHaveCount(3);
  await expect(page.locator("body")).not.toContainText("private document body");
  await expect(page.locator("body")).not.toContainText("secret-token");
});

test("an expanded workflow shows its real embedding projection", async ({ page }) => {
  const row = api.getData("trajectories")[0];
  api.setData("trajectories", [{
    ...row,
    promotedWorkflowId: 44,
    promotedWorkflowName: "Repair policy documentation",
    promotedWorkflowStatus: "active",
    workflowObservationCount: 3,
    promotedWorkflowEmbeddingMap: {
      profile: "openai:text-embedding-3-small:dimensions=768:muvera-unit-v1",
      points: [
        { kind: "intent", label: "Repair policy documentation", x: -0.1, y: 0.05 },
        { kind: "phase", label: "Discover", x: -0.8, y: 0.7 },
        { kind: "tool", label: "search", x: 1, y: -0.6 },
      ],
    },
  }]);
  await page.goto("/workflows");
  await page.getByText("3 chat observations in this workflow", { exact: true }).click();
  await expect(page.getByRole("figure", { name: "Workflow embedding" })).toBeVisible();
  await expect(page.getByRole("img", { name: "Embedding projection with 3 points" })).toBeVisible();
  await expect(page.getByText("openai:text-embedding-3-small:dimensions=768:muvera-unit-v1", { exact: true })).toBeVisible();
});

test("a human can harvest and codify a proposed workflow", async ({ page }) => {
  await page.goto("/workflows");
  await page.getByRole("button", { name: "Harvest new workflows" }).click();
  await expect(page.getByRole("dialog", { name: "Harvest new workflows" })).toBeVisible();
  await page.getByRole("button", { name: "Analyze recent turns" }).click();
  await expect(page.getByLabel("Candidate 1 name")).toHaveValue("Answer retention questions");
  await expect(page.getByLabel("Candidate 2 name")).toHaveValue("what are the top capabilities of mari");
  await expect(page.getByLabel("Select candidate 2")).not.toBeChecked();
  await expect(page.getByText("Update the retention documentation", { exact: true })).toBeHidden();
  await page.getByText("1 supporting turn", { exact: true }).first().click();
  await expect(page.getByText("Update the retention documentation", { exact: true })).toBeVisible();
  await page.getByLabel("Candidate 1 name").fill("Answer policy retention questions");
  await page.getByRole("button", { name: "Codify selected" }).click();
  await expect(page.getByRole("heading", { name: "Workflows codified" })).toBeVisible();
  await expect.poll(() => api.calls.some((call) => call.query.includes("promoteTrajectoryToWorkflow")
    && call.variables.name === "Answer policy retention questions")).toBeTruthy();
});

test("a human can tune evidence and tool calls before codifying a trajectory", async ({ page }) => {
  await page.goto("/workflows");
  await page.getByRole("button", { name: "Inspect run" }).first().click();
  await page.getByLabel("search disposition").selectOption("preferred");
  await page.getByText("Tune arguments", { exact: true }).first().click();
  await page.getByLabel("search arguments").fill('{"query":"mari retention"}');
  await page.getByRole("button", { name: "Save tool" }).click();
  await expect.poll(() => api.calls.some((call) => call.query.includes("tuneTrajectoryStep")
    && call.variables.disposition === "preferred")).toBeTruthy();

  await page.getByLabel("Retention runbook relevance").selectOption("pinned");
  await page.getByLabel("Retention runbook note").fill("Canonical evidence");
  await page.getByRole("button", { name: "Save", exact: true }).last().click();
  await expect.poll(() => api.calls.some((call) => call.query.includes("tuneTrajectoryEvidence")
    && call.variables.relevance === "pinned")).toBeTruthy();

  await page.getByLabel("Workflow name").fill("Retention answer workflow");
  await page.getByRole("button", { name: "Codify workflow" }).click();
  // Promotion answers in place: the drawer stays open on the run and shows the
  // codified workflow's panel. Nothing runs until a human enables it.
  await expect(page).toHaveURL(/\/workflows\?trajectory=1$/);
  const drawer = page.getByRole("dialog");
  await expect(drawer.getByText("Paused", { exact: true })).toBeVisible();
  await drawer.getByRole("button", { name: "Enable workflow" }).click();
  await expect.poll(() => api.calls.some((call) => call.query.includes("setAssistantWorkflowEnabled")
    && call.variables.enabled === true)).toBeTruthy();
  await expect(drawer.getByText("Enabled for assistants", { exact: true })).toBeVisible();
  await expect(drawer.getByRole("button", { name: "Pause workflow" })).toBeVisible();
  await drawer.getByRole("button", { name: "Cache reviewed answer" }).click();
  await expect.poll(() => api.calls.some((call) => call.query.includes("setAssistantWorkflowCache")
    && call.variables.enabled === true)).toBeTruthy();
  await expect(drawer.getByText("Current", { exact: true })).toBeVisible();
});

test("stale reviewed-answer workflows can be reconciled together", async ({ page }) => {
  const row = api.getData("trajectories")[0];
  api.setData("trajectories", [{
    ...row, promotedWorkflowId: 44, promotedWorkflowStatus: "active",
    promotedWorkflowCachePolicy: "reviewed_answer", promotedWorkflowCacheState: "stale",
    promotedWorkflowDependencyCount: 2, promotedWorkflowCacheRefreshedAt: "2026-08-20T12:00:00Z",
  }]);
  await page.goto("/workflows");
  await page.getByRole("button", { name: "Reconcile stale caches (1)" }).click();
  await expect.poll(() => api.calls.some((call) =>
    call.query.includes("reconcileStaleAssistantWorkflows"))).toBeTruthy();
});

test("a stale ?tab=scheduled link lands on the Scheduled tasks page", async ({ page }) => {
  await page.goto("/workflows?tab=scheduled");
  await expect(page).toHaveURL(/\/scheduled-tasks$/);
  await expect(page.getByRole("heading", { name: "Scheduled tasks" })).toBeVisible();
});

test("scheduled tasks can be paused, rescheduled, and run without losing their cadence", async ({ page }) => {
  await page.goto("/scheduled-tasks");
  await expect(page.getByRole("heading", { name: "Scheduled tasks" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Fact review" })).toBeVisible();
  await expect(page.getByText("#1801 waiting", { exact: false })).toBeVisible();

  await page.getByRole("button", { name: "Pause" }).click();
  await expect.poll(() => api.calls.some((call) => call.query.includes("setWorkflowStatus")
    && call.variables.status === "paused")).toBeTruthy();
  await expect(page.getByRole("button", { name: "Resume" })).toBeVisible();
  await expect(page.getByLabel("Fact review cadence")).toHaveValue("60");

  await page.getByLabel("Fact review cadence").selectOption("");
  await expect.poll(() => api.calls.some((call) => call.query.includes("setWorkflowTrigger")
    && String(call.variables.trigger).includes('"on":""'))).toBeTruthy();
  await expect(page.getByText("Manual", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Run now" }).click();
  await expect.poll(() => api.calls.some((call) => call.query.includes("runWorkflow"))).toBeTruthy();
  await expect(page.getByText("Run #1802 started.", { exact: false })).toBeVisible();

  // The mock persists the writes, so a reload proves the page renders the
  // stored state rather than its own optimistic memory of it.
  await page.reload();
  await expect(page.getByRole("heading", { name: "Fact review" })).toBeVisible();
  await expect(page.getByText("Manual", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Fact review cadence")).toHaveValue("");
});

test("sync rows offer the sub-hourly cadences that Sources set", async ({ page }) => {
  api.setData("workflows", [{
    id: 11, name: "Sync acme/handbook", description: "Keeps acme/handbook indexed.",
    color: "#5c7a4c", pinned: false, status: "active",
    trigger: { on: "schedule", every_minutes: 10 }, scheduleCapable: true,
    lastRunNumber: 1901, lastRunStatus: "passed", lastRunStarted: "2026-08-19T12:00:00Z",
    nodes: [{ kind: "trigger", label: "Every 10 min", config: {} },
            { kind: "sync_source", label: "Sync", config: { source_id: 1 } }],
  }]);
  await page.goto("/scheduled-tasks");
  const cadence = page.getByLabel("Sync acme/handbook cadence");
  // a real option, not a synthesized orphan you could leave but never rejoin
  await expect(cadence).toHaveValue("10");
  await expect(cadence.locator('option[value="15"]')).toHaveText("Every 15 min");
});

test("a removed recurring job can be scheduled again with New task", async ({ page }) => {
  await page.goto("/scheduled-tasks");
  await page.getByRole("button", { name: "New task" }).click();
  const dialog = page.getByRole("dialog", { name: "New scheduled task" });
  await dialog.getByLabel("Task kind").selectOption("digest");
  await dialog.getByLabel("Task cadence").selectOption("10080");
  await dialog.getByRole("button", { name: "Create task" }).click();
  await expect.poll(() => api.calls.some((call) => call.query.includes("createScheduledTask")
    && call.variables.kind === "digest" && call.variables.everyMinutes === 10080)).toBeTruthy();
  await expect(page.getByRole("heading", { name: "Weekly digest refresh" })).toBeVisible();
});

test("a scheduled task can be removed from the task manager", async ({ page }) => {
  await page.goto("/scheduled-tasks");
  await expect(page.getByRole("heading", { name: "Fact review" })).toBeVisible();
  await page.getByRole("button", { name: "Remove" }).click();
  await page.getByRole("button", { name: "Confirm remove" }).click();
  await expect.poll(() => api.calls.some((call) => call.query.includes("removeScheduledTask")
    && call.variables.taskId === 1)).toBeTruthy();
  await expect(page.getByRole("heading", { name: "Fact review" })).toHaveCount(0);
  // Server-driven, not the row's local removed flag: the mock deleted it.
  await page.reload();
  await expect(page.getByRole("heading", { name: "Fact review" })).toHaveCount(0);
  await expect(page.getByText("No scheduled tasks", { exact: true })).toBeVisible();
});

test("scheduled tasks read failures replace the list rather than masquerading as empty", async ({ page }) => {
  await page.route("**/graphql", async (route) => {
    const query = (route.request().postDataJSON() as { query?: string }).query ?? "";
    if (query.includes("query ScheduledTasks")) {
      await route.fulfill({ json: { errors: [{ message: "Scheduled tasks are temporarily unavailable." }] } });
    } else {
      await route.fallback();
    }
  });
  await page.goto("/scheduled-tasks");
  await expect(page.getByText("Scheduled tasks are temporarily unavailable.", { exact: false })).toBeVisible();
  await expect(page.getByText("No scheduled tasks", { exact: true })).toHaveCount(0);
});

test("a codified workflow can be deleted without deleting its observed trajectory", async ({ page }) => {
  const row = api.getData("trajectories")[0];
  api.setData("trajectories", [{
    ...row, promotedWorkflowId: 44, promotedWorkflowName: "Repair policy documentation",
    promotedWorkflowStatus: "active",
    promotedWorkflow: { id: 44, name: "Repair policy documentation", status: "active", nodeCount: 3 },
    promotedWorkflowCachePolicy: "none", promotedWorkflowCacheState: "disabled",
  }]);
  await page.goto("/workflows");
  // The codified workflow is managed in place on the run's card.
  await page.getByRole("button", { name: "Delete workflow" }).click();
  await page.getByRole("button", { name: "Confirm delete" }).click();
  await expect.poll(() => api.calls.some((call) => call.query.includes("deleteAssistantWorkflow")
    && call.variables.workflowId === 44)).toBeTruthy();
  await expect(page.getByText("Workflow deleted. The observed run is kept.", { exact: true }).first()).toBeVisible();
});

test("trajectory taxonomy filter and pagination remain URL-addressable", async ({ page }) => {
  const rows = Array.from({ length: 60 }, (_, index) => ({
    id: index + 1, sessionId: index + 100, prompt: `Task ${index + 1}`, status: "ready",
    model: "ollama:gemma3:4b", layer1: `Grounded workflow ${index + 1}`,
    layer2: `Activity ${index + 1}`, category: index % 2 ? "Incident response" : "Documentation maintenance",
    macroIntent: `Intent ${index + 1}`, phases: [], stepCount: 1, failureCount: 0, reworkCount: 0,
    startedAt: "2026-08-19T12:00:00Z", completedAt: "2026-08-19T12:00:01Z",
    steps: [{ ordinal: 0, tool: "search", actionFamily: "discover", args: {}, summary: "one hit", ok: true }],
  }));
  api.setData("trajectories", rows);
  api.setData("trajectoryCategories", ["Documentation maintenance", "Incident response"]);
  await page.goto("/workflows");
  await expect(page.locator("article")).toHaveCount(25);
  await page.getByLabel("Filter by category").selectOption("Incident response");
  await expect(page).toHaveURL(/category=Incident(?:\+|%20)response/);
  await expect.poll(() => api.calls.some((call) => call.query.includes("query Workflows") && call.variables.category === "Incident response")).toBeTruthy();
  await expect(page.locator("article")).toHaveCount(25);
  await page.getByRole("button", { name: "Next page" }).click();
  await expect(page).toHaveURL(/offset=25/);
  await expect(page.locator("article")).toHaveCount(5);
});

test("a 5,000-row trajectory archive renders only one bounded page", async ({ page }) => {
  const base = {
    sessionId: 1, prompt: "Task", status: "ready", model: "ollama:gemma3:4b",
    layer1: "Searched and inspected evidence.", layer2: "Investigated documentation.",
    category: "Investigation", macroIntent: "Investigate knowledge", phases: [],
    stepCount: 1, failureCount: 0, reworkCount: 0, startedAt: "2026-08-19T12:00:00Z",
    completedAt: "2026-08-19T12:00:01Z", steps: [],
  };
  api.setData("trajectories", Array.from({ length: 5000 }, (_, index) => ({ ...base, id: index + 1 })));
  api.setData("trajectoryCategories", ["Investigation"]);
  await page.goto("/workflows");
  await expect(page.getByText("Showing 1 to 25 of 5,000 workflows", { exact: true })).toBeVisible();
  await expect(page.locator("article")).toHaveCount(25);
  expect(await page.locator("article").count()).toBeLessThanOrEqual(25);
});

test("large lineage opens as a comprehensible aggregate instead of a 35-node hairball", async ({ page }) => {
  const count = 2000;
  const nodes = Array.from({ length: count }, (_, index) => ({
    id: `doc-${index + 1}`, docId: index + 1, title: `Document ${index + 1}`, source: index % 2 ? "github" : "docs",
    docKind: "page", icon: "file", x: 0.24 + (index % 20) * 0.03, y: 0.2 + (index % 30) * 0.02,
    pinned: false, date: "2026-08-19", createdDate: "2026-08-18", warn: index % 17 === 0,
    owner: `Owner ${index % 50}`, tags: [], staleDays: 0, orphan: false,
    inbound: index ? 1 : 0, outbound: index < count - 1 ? 1 : 0, group: "", meta: "Large corpus",
  }));
  const edges = Array.from({ length: count - 1 }, (_, index) => ({
    id: index + 1, fromId: `doc-${index + 1}`, toId: `doc-${index + 2}`,
    kind: "references", date: "2026-08-19", meta: null,
  }));
  api.setData("lineage", nodes);
  api.setData("lineageEdges", edges);
  api.setData("graphStats", { docs: count, edges: edges.length, sources: 2, people: 50,
    activity: [{ date: "2026-08-19", count }] });
  await page.goto("/lineage");
  await page.getByRole("button", { name: "Overview", exact: true }).click();
  await expect(page.getByText(/2 groups · 2,000 documents, rolled up/i)).toBeVisible();
  await expect(page.getByRole("group", { name: /Documents\. Use the arrow keys/ }).getByRole("button")).toHaveCount(2);
  await expect(page.getByText("Document 1", { exact: true })).toHaveCount(0);
  await page.getByRole("group", { name: /Documents\. Use the arrow keys/ })
    .getByRole("button", { name: /GitHub · 1,000 documents/i }).click();
  await expect(page.getByText("Rolled-up group", { exact: true })).toBeVisible();
  await expect(page.getByText(/Showing 1 to 5 of 1,000 members/i)).toBeVisible();
  const horizontalOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(horizontalOverflow).toBeLessThanOrEqual(2);
});

test("trajectory read failures replace the archive rather than masquerading as empty", async ({ page }) => {
  await page.route("**/graphql", async (route) => {
    const query = (route.request().postDataJSON() as { query?: string }).query ?? "";
    if (query.includes("query Workflows")) {
      await route.fulfill({ json: { errors: [{ message: "Iceberg catalog unavailable" }] } });
    } else {
      await route.fallback();
    }
  });
  await page.goto("/workflows");
  await expect(page.getByText("Iceberg catalog unavailable", { exact: true })).toBeVisible();
  await expect(page.getByText("No workflows observed yet", { exact: true })).toHaveCount(0);
});
