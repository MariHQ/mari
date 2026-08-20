/* Fixtures + assertions for `npm run smoke` (see smoke.mjs, which bundles and
   runs this). One block per page that has a real adapter. */

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { PageModule } from "@mari-design/components/pages";
import { page as overview } from "@mari-design/components/pages/OverviewPage";
import { page as tasks } from "@mari-design/components/pages/TasksPage";
import { page as facts } from "@mari-design/components/pages/FactsPage";
import { page as decisions } from "@mari-design/components/pages/DecisionsPage";
import { page as knowledge } from "@mari-design/components/pages/KnowledgePage";
import { page as insights } from "@mari-design/components/pages/InsightsPage";
import { page as trajectories } from "@mari-design/components/pages/TrajectoriesPage";
import { page as audit } from "@mari-design/components/pages/AuditPage";
import { page as members } from "@mari-design/components/pages/SettingsMembersPage";
import { page as apiKeys } from "@mari-design/components/pages/SettingsApiKeysPage";
import { page as auditLog } from "@mari-design/components/pages/SettingsAuditLogPage";
import { page as docReview } from "@mari-design/components/pages/DocReviewPage";
import { page as answers } from "@mari-design/components/pages/AnswersPage";
import { page as lineage } from "@mari-design/components/pages/LineagePage";
import { page as flows } from "@mari-design/components/pages/FlowsPage";
import { page as library } from "@mari-design/components/pages/LibraryPage";
import { page as publish } from "@mari-design/components/pages/PublishPage";
import { page as sources } from "@mari-design/components/pages/SourcesPage";
import { page as settingsGeneral } from "@mari-design/components/pages/SettingsGeneralPage";
import { page as settingsModels } from "@mari-design/components/pages/SettingsModelsPage";
import { page as welcome } from "@mari-design/components/pages/WelcomePage";
import { page as login } from "@mari-design/components/pages/LoginPage";
import { page as setup } from "@mari-design/components/pages/SetupPage";
import { buildLogin, buildSetup } from "../src/data/auth-pages";
import { buildAnswers, EMPTY as ANSWERS_EMPTY, mapAnswers, mapHarvestSources } from "../src/data/answers";
import { buildFlows, EMPTY as FLOWS_EMPTY } from "../src/data/flows";
import { buildLibrary, EMPTY as LIBRARY_EMPTY } from "../src/data/library";
import { buildLineage, EMPTY as LINEAGE_EMPTY } from "../src/data/lineage";
import { buildPublish, pickSiteRow, EMPTY as PUBLISH_EMPTY } from "../src/data/publish";
import { RULE_COUNT } from "@mari-design/components/features/LibraryRulesPanel";
import { buildSettingsGeneral, EMPTY as GENERAL_EMPTY } from "../src/data/settings-general";
import { buildSettingsModels, EMPTY as MODELS_EMPTY } from "../src/data/settings-models";
import { buildSources, EMPTY as SOURCES_EMPTY } from "../src/data/sources";
import { buildWelcome, EMPTY as WELCOME_EMPTY } from "../src/data/welcome";
import { EMPTY as DOC_REVIEW_EMPTY, mapDocReview } from "../src/data/doc-review";
import { buildAudit, EMPTY as AUDIT_EMPTY } from "../src/data/audit";
import { buildAuditLog, mapAuditLog, mapDetails } from "../src/data/audit-log";
import { buildDecisions, mapDecisions } from "../src/data/decisions";
import { buildFacts, mapBanner, mapFacts } from "../src/data/facts";
import { mapSearch } from "../src/data/knowledge";
import { mapFreshness, mapWidgets } from "../src/data/insights";
import { EMPTY, mapOverview } from "../src/data/overview";
import { buildApiKeys, buildMembers, mapApiKeys, mapGithubTeam, mapMembers } from "../src/data/settings";
import { buildTasks, mapAssignees, mapStrip, mapTasks } from "../src/data/tasks";
import { buildTrajectories, EMPTY as TRAJECTORIES_EMPTY } from "../src/data/trajectories";
import { buildFocusedGraph, buildOverviewGraph } from "@mari-design/components/features/LineageDataModel";

/* This file used to install a DOM shim so `DocReviewOutlinePanel` could be
   server-rendered: it derived its outline through `document.createElement`
   inside a `useMemo`, which threw on any server. The panel now parses text
   directly and touches no DOM, so the shim is gone. */

let failures = 0;
function check(label: string, cond: boolean) {
  if (!cond) { failures++; console.error(`  FAIL ${label}`); }
}

const render = (p: PageModule<any, any>, props: object) =>
  renderToStaticMarkup(createElement(p.component as any, props));

/** Every page must survive its universal states. `loading` and `error` are
    driven by props, and `empty` must fall out of the data — a page that only
    renders its empty state because it was told to has never been tested
    against a real new workspace.

    The error branch is asserted to *differ* from the normal body, not to echo
    the string: some pages surface the message verbatim (Overview), others
    render catalogued copy instead (Insights, CONVENTIONS §8). Both are the
    library's call. What this app owes either of them is a real message. */
function states(p: PageModule<any, any>, empty: any, opts: { errorIgnored?: boolean } = {}) {
  const blank = render(p, { data: empty, loading: false, error: null });
  check(`${p.id}: loading renders`, render(p, { data: empty, loading: true, error: null }).length > 500);
  const errored = render(p, { data: empty, loading: false, error: "API offline" });
  // `errorIgnored` pins a page that never reads its `error` prop, so the day
  // the library starts honouring it this assertion fails and gets deleted.
  check(`${p.id}: error renders its own branch`,
    opts.errorIgnored ? errored === blank : errored !== blank);
  check(`${p.id}: empty renders`, blank.length > 500);
  check(`${p.id}: mobile renders`, render(p, { data: empty, loading: false, error: null, mobile: true }).length > 500);
}

/* ── Overview ───────────────────────────────────────────────────────────── */

/** A plausible response to src/data/overview.ts's QUERY, shaped exactly as the
    Strawberry resolvers return it. Deliberately includes an unknown step kind
    and a step with no run row, because real workspaces have both. */
const OVERVIEW_RES: any = {
  overviewStats: { changes: 47, factsReview: 6, flowsRunning: 3 },
  tasks: [{ id: 1, title: "Verify the proration rule", assigneeInitials: "DR", kind: "factcheck", kindLabel: "Fact check", done: false }],
  digest: [{ title: "Billing docs realigned", summary: "Three pages drifted.", where: [{ source: "notion", label: "Pricing FAQ" }], impact: [{ name: "Support", tone: "info" }] }],
  activityFeed: [{ id: 1, kind: "run", actor: "Docs guardrail", text: "completed a run over", target: "billing/*.md", secondsAgo: 42 }],
  search: [{ id: 101, source: "notion", title: "Pricing FAQ", date: "2026-07-20" }],
  sourcePulse: [{ provider: "github", name: "GitHub", status: "active", stat: "128", unit: "commits", bars: [4, 7, 5, 9, 6, 11, 8] }],
  workflows: [{ id: 7, name: "Docs guardrail", status: "active", nodes: [
    { kind: "trigger", label: "When docs change" },
    { kind: "notify", label: "Post to Slack" },
    { kind: "not_a_real_step", label: "From a newer flow editor" },
  ] }],
  workflowRuns: [{ id: 900, workflowId: 7, status: "passed", started: "2026-07-20T14:57:00", rows: [{ step: "Post to Slack", status: "passed" }] }],
};

console.log("overview");
/* The greeting reads the reader's own clock in the zone Preferences stores,
   and the dashboard counts over a window the app can change. Both are data
   the adapter passes through. */
const data = mapOverview(OVERVIEW_RES, "Dana", "America/Los_Angeles", { preset: "30d" });

check("drops step kinds the library cannot draw", data.flow?.nodes.length === 2);
check("carries per-step outcomes from the last run",
  data.flow?.nodes.find((n) => n.label === "Post to Slack")?.state === "succeeded");
check("leaves un-run steps without an outcome",
  data.flow?.nodes.find((n) => n.label === "When docs change")?.state === undefined);
check("passes dates through unformatted", data.docs[0]?.date === "2026-07-20");

check("the greeting's zone is the account's, not this machine's",
  data.timeZone === "America/Los_Angeles");
check("the counting window reaches the page", data.range?.preset === "30d");
const def = render(overview, { data, loading: false, error: null });
/* The greeting is derived from the clock now, so asserting "Good morning"
   would pin this suite to the hour it was written in. What must hold is that
   the reader is greeted BY NAME, with one of the three real greetings. */
check("greets by given name",
  ["Good morning", "Good afternoon", "Good evening"].some((g) => def.includes(`${g}, Dana`)));
check("renders digest content", def.includes("Pricing FAQ"));
check("formats the ISO date for display", def.includes("Jul 20, 2026"));
check("empty state derives from the data",
  render(overview, { data: EMPTY, loading: false, error: null }).includes("Connect your first source"));
check("error is shown verbatim",
  render(overview, { data: EMPTY, loading: false, error: "API offline" }).includes("API offline"));
states(overview, EMPTY);

/* ── Tasks ──────────────────────────────────────────────────────────────── */

console.log("tasks");
const TASKS_RES: any = {
  reviewItems: { items: [
    { id: "task:1", title: "Verify the proration rule", assignee: "Dana Rodriguez", kind: "task", status: "pending", source: "Manual", due: "2026-07-18" },
    { id: "task:2", title: "Approve the SSO guide", assignee: "Morgan Green", kind: "task", status: "done", source: "Manual", due: "" },
  ], totalCount: 2, pageInfo: { endCursor: "2", hasNextPage: false } },
  /* Who a task can be filed to. Without this the composer draws no owner
     picker and every task silently files to whoever is signed in. */
  members: [
    { id: 1, name: "Dana Rodriguez", initials: "DR", status: "active" },
    { id: 2, name: "Priya Kapoor", initials: "PK", status: "invited" },
  ],
};
const taskRows = mapTasks(TASKS_RES);
check("tasks: a due date arrives as an ISO date, not a formatted one", taskRows[0].due === "2026-07-18");
check("tasks: a task with no deadline carries none", taskRows[1].due === undefined);
check("tasks: overdue comes off the server, not off a clock here", taskRows[0].overdue === true);
const taskStrip = mapStrip(TASKS_RES)!;
check("tasks: the strip is the server's rollup of the same rows",
  taskStrip.statValue === "2" && taskStrip.statLabel === "open" && taskStrip.people.includes("DR"));
check("tasks: an empty inbox has nothing to summarise",
  mapStrip({ reviewItems: { items: [], totalCount: 0, pageInfo: { endCursor: "", hasNextPage: false } } } as any) === null);
const taskAssignees = mapAssignees(TASKS_RES);
check("tasks: a task cannot be filed to someone who has never signed in",
  taskAssignees.length === 1 && taskAssignees[0].name === "Dana Rodriguez");
const tasksData = buildTasks(taskRows, taskStrip, "", taskAssignees);
check("tasks: the strip reaches the page", tasksData.strip !== null);
check("tasks: no priority vocabulary means no priority control",
  tasksData.priorities === undefined);
const tasksHtml = render(tasks, { data: tasksData, loading: false, error: null });
check("tasks: renders both columns", tasksHtml.includes("Verify the proration rule") && tasksHtml.includes("Approve the SSO guide"));
check("tasks: renders the strip headline", tasksHtml.includes("Review queue"));
/* The row formats the ISO date itself now (§5, P-TA-4), so the raw value must
   NOT reach the screen: an assertion on "2026-07-18" would pass only while the
   page was echoing an unformatted string. */
check("tasks: formats the due date rather than echoing the ISO value",
  tasksHtml.includes("Jul 18, 2026") && !tasksHtml.includes("2026-07-18"));
states(tasks, buildTasks([], null, ""));

/* ── Facts ──────────────────────────────────────────────────────────────── */

console.log("facts");
const FACTS_RES: any = {
  facts: [
    { id: 1, claim: "Growth tier prorates monthly", source: "Billing runbook", owner: "Dana R.", status: "Verified", verified: "2026-07-14" },
    { id: 2, claim: "SSO is admin-only", source: "auth/README", owner: "Priya K.", status: "Needs evidence", verified: "" },
  ],
  factContradictions: [
    { factId: 1, claim: "Retention is 100 days", otherFactId: 2, otherClaim: "Retention is 250 days", reason: "numeric", detail: "100 vs 250" },
  ],
};
const factRows = mapFacts(FACTS_RES);
check("facts: unverified rows carry no verification date", factRows[1].verified === null);
const factsBanner = mapBanner(FACTS_RES)!;
check("facts: the banner quotes both stored claims",
  factsBanner.body.includes("Retention is 100 days") && factsBanner.body.includes("Retention is 250 days"));
check("facts: the banner names why they disagree", factsBanner.body.includes("100 vs 250"));
check("facts: a consistent ledger raises no banner",
  mapBanner({ facts: [], factContradictions: [] } as any) === null);
const factsData = buildFacts(factRows, factsBanner);
check("facts: tab counts come off the rows",
  factsData.filters.find((f) => f.id === "verified")?.count === 1);
const factsHtml = render(facts, { data: factsData, loading: false, error: null });
check("facts: renders claims", factsHtml.includes("Growth tier prorates monthly"));
check("facts: renders the contradiction banner", factsHtml.includes("Two claims contradict each other"));
states(facts, buildFacts([], null));

/* ── Decisions ──────────────────────────────────────────────────────────── */

console.log("decisions");
const decisionRows = mapDecisions({ decisions: [
  { id: 1, statement: "Adopt the Growth tier rename", context: "Pricing sync", status: "proposed", sourceLabel: "Slack · #pricing", owners: ["DR"], decidedOn: "", supersededBy: null, supersededByStatement: "", impactSummary: "3 docs affected", impactCount: 3 },
  { id: 2, statement: "SSO ships self-serve", context: "", status: "not_a_status", sourceLabel: "Granola · Postmortem", owners: [], decidedOn: "2026-07-10", supersededBy: null, supersededByStatement: "", impactSummary: "", impactCount: 0 },
] });
check("decisions: unknown status falls back to proposed", decisionRows[1].status === "proposed");
check("decisions: provider comes off the source label", decisionRows[0].provider === "slack");
const decisionsData = buildDecisions(decisionRows, "all");
const decisionsHtml = render(decisions, { data: decisionsData, loading: false, error: null });
check("decisions: renders statements", decisionsHtml.includes("Adopt the Growth tier rename"));
/* The rail derives what awaits sign-off from the RECORDS (`data.awaiting` is
   deprecated and unread), so what has to hold is that an unsigned proposal
   reaches the rail with a control that can sign it — the id-less version could
   only ever chip itself "Ratified" locally (P-DE-3). */
check("decisions: the rail offers sign-off on an unsigned proposal",
  decisionsHtml.includes("Awaiting sign-off") && decisionsHtml.includes("Ratify"));
/* A ledger whose every record is already signed still draws the rail, and the
   rail says what it means rather than rendering an empty box (P-DE-4). */
const signedRows = decisionRows.map((d) => ({ ...d, status: "ratified" as const }));
check("decisions: a fully signed ledger says so instead of drawing a blank rail",
  render(decisions, { data: buildDecisions(signedRows, "all"), loading: false, error: null })
    .includes("Nothing awaiting sign-off"));
states(decisions, buildDecisions([], "all"));

/* ── Knowledge ──────────────────────────────────────────────────────────── */

console.log("knowledge");
const KNOWLEDGE_RES: any = { search: [
  { id: 101, source: "notion", title: "Pricing FAQ", snippet: "Growth tier proration…", kind: "page", author: "Dana R.", authorInitials: "DR", date: "2026-07-20", tags: ["canonical"] },
  { id: 102, source: "github", title: "Add SAML walkthrough", snippet: "Okta setup", kind: "pr", author: "Priya K.", authorInitials: "PK", date: "2026-07-19", tags: [] },
  { id: 103, source: "github", title: "Bump deps", snippet: "", kind: "commit", author: "Sam L.", authorInitials: "SL", date: "2026-07-18", tags: [] },
], searchTotal: 248 };
const { results, total: knowledgeTotal } = mapSearch(KNOWLEDGE_RES);
check("knowledge: maps known kinds", results[1].kind === "pr");
check("knowledge: unknown kinds read as pages", results[2].kind === "page");
/* `total` is the corpus-wide count behind one page of hits. Without it the
   footer would state the page size as the answer. */
check("knowledge: the total is the whole match, not the page", knowledgeTotal === 248);
const knowledgeHtml = render(
  knowledge,
  { data: { results, doc: null, total: knowledgeTotal }, loading: false, error: null },
);
check("knowledge: renders hits", knowledgeHtml.includes("Pricing FAQ"));
check("knowledge: the footer counts the whole match, not the page",
  knowledgeHtml.includes("248"));
states(knowledge, { results: [], doc: null });

/* ── Insights ───────────────────────────────────────────────────────────── */

console.log("insights");
const INSIGHTS_RES: any = {
  insightStats: { searches: 412, answersServed: 96, driftCaught: 14, docsFixed: 31, since: "2026-05-01T00:00:00" },
  readability: [
    { id: 1, title: "Pricing FAQ", source: "notion", grade: "B", note: "Two long sentences" },
    { id: 2, title: "Never scored", source: "github", grade: "", note: "" },
  ],
  glossaryCandidates: [{ id: 1, term: "Proration", variants: "pro-rate, prorated", definition: "Charging for partial periods." }],
  /* `source` is the workspace's own name for the connector, `provider` the key
     the chart draws a mark from. The chart no longer holds a table turning
     "github" into a repository name, so both have to arrive. */
  freshness: [
    { source: "GitHub · acme/handbook", provider: "github", fresh: 40, aging: 12, stale: 3 },
    { source: "slack", provider: "slack", fresh: 18, aging: 2, stale: 0 },
  ],
  auditLog: [{ id: 1, actor: "Mari", verb: "scored", target: "Pricing FAQ", at: "2026-07-20T14:57:00" }],
};
const widgets = mapWidgets(INSIGHTS_RES)!;
check("insights: drops documents with no readability pass", widgets.readability.length === 1);
check("insights: splits glossary variants into chips", widgets.glossary[0].variants.length === 2);
check("insights: tiles carry real counts", widgets.stats.find((s) => s.key === "searches")?.value === 412);
check("insights: no stats means no widget block", mapWidgets({ ...INSIGHTS_RES, insightStats: null }) === null);
const freshness = mapFreshness(INSIGHTS_RES);
check("insights: a source names itself when it says more than its provider key",
  freshness[0].label === "GitHub · acme/handbook" && freshness[0].source === "github");
check("insights: a source that only repeats its provider key gets no label",
  freshness[1].label === undefined);
const insightsHtml = render(
  insights,
  { data: { widgets, freshness, extras: null }, loading: false, error: null },
);
check("insights: renders", insightsHtml.length > 500);
check("insights: the chart names the source the workspace named",
  insightsHtml.includes("GitHub · acme/handbook"));
states(insights, { widgets: null, freshness: null, extras: null });

/* ── Agent trajectories ────────────────────────────────────────────────── */

console.log("trajectories");
const trajectoryData = buildTrajectories({
  trajectories: [{
    id: 1, sessionId: 1, prompt: "Fix docs", status: "ready", model: "ollama:gemma3:4b",
    layer1: "Searched and updated one document.", layer2: "Updated documentation.",
    category: "Documentation", macroIntent: "Repair docs", phases: [], stepCount: 2,
    failureCount: 0, reworkCount: 0, startedAt: "2026-08-19T12:00:00Z",
    completedAt: "2026-08-19T12:00:01Z", steps: [],
  }], trajectoryTotal: 1, trajectoryCategories: ["Documentation"],
}, null, 0);
const trajectoryHtml = render(trajectories, { data: trajectoryData, loading: false, error: null });
check("trajectories: renders inferred macro intent", trajectoryHtml.includes("Repair docs"));
check("trajectories: renders bounded count", trajectoryHtml.includes("Showing 1-1 of 1"));
states(trajectories, TRAJECTORIES_EMPTY);

/* ── Audit ──────────────────────────────────────────────────────────────── */

console.log("audit");
const AUDIT_RES: any = {
  auditRuns: [
    { id: 9, provider: "github", repo: "acme/product-docs", findings: 4, fixed: 1, ranAt: "2026-07-20T14:57:00" },
    { id: 8, provider: "github", repo: "acme/product-docs", findings: 7, fixed: 7, ranAt: "2026-07-13T09:00:00" },
  ],
  auditFindings: [
    { id: 1, runId: 9, kind: "Tags", title: "Untagged pages", detail: "12 pages have no tag", fixAction: "apply_tag", fixPayload: { tag: "needs-review" }, status: "open" },
    { id: 2, runId: 9, kind: "Wormholes", title: "From a newer checker", detail: "", fixAction: "apply_tag", fixPayload: null, status: "open" },
    { id: 3, runId: 8, kind: "Coverage", title: "From an older run", detail: "", fixAction: "ingest", fixPayload: null, status: "fixed" },
  ],
  members: [{ id: 1, name: "Dana R." }],
};
const auditData = buildAudit(AUDIT_RES);
check("audit: shows only the latest run's findings", auditData.findings.length === 1);
check("audit: marks the current run in the history", auditData.history[0].current === true);
check("audit: summarizes the run", auditData.summary === "4 findings, 1 fixed.");
const auditHtml = render(audit, { data: auditData, loading: false, error: null });
check("audit: renders", auditHtml.includes("Untagged pages"));
/* `ranAt` is an ISO timestamp on the run and on every history row (P-AU-3);
   the page formats both. The raw value must not reach the screen. */
check("audit: formats the run timestamps rather than echoing them",
  auditHtml.includes("Jul 20, 2026") && auditHtml.includes("Jul 13, 2026")
  && !auditHtml.includes("2026-07-20T14:57:00"));
check("audit: no runs means no repo, which is the connect state", buildAudit({ auditRuns: [], auditFindings: [], members: [] } as any).repo === "");
states(audit, AUDIT_EMPTY);

/* ── Settings → Members ─────────────────────────────────────────────────── */

console.log("settings-members");
const MEMBERS_RES: any = {
  members: [
    { id: 1, name: "Dana Rodriguez", initials: "DR", email: "dana@acme.com", role: "admin", status: "active", joined: "2026-01-14" },
    { id: 2, name: "Priya Kapoor", initials: "PK", email: "priya@acme.com", role: "user", status: "invited", joined: "2026-07-01" },
  ],
  workspace: { name: "Acme Data Platform" },
  provisioning: { githubTeam: { team: "acme/docs-team", connected: true } },
};
const memberRows = mapMembers(MEMBERS_RES);
const memberTeam = mapGithubTeam(MEMBERS_RES);
check("members: the GitHub team is the one the server says is configured",
  memberTeam.team === "acme/docs-team" && memberTeam.connected === true);
check("members: no credential means not connected, whatever team is stored",
  mapGithubTeam({ provisioning: { githubTeam: { team: "acme/docs-team", connected: false } } } as any).connected === false);
const membersData = buildMembers(memberRows, MEMBERS_RES.workspace.name, memberTeam, "none");
check("members: rail counts come off the roster",
  membersData.summary.find((s) => s.label === "Pending invites")?.value === "1");
check("members: the workspace name reaches the page", membersData.workspaceName === "Acme Data Platform");
const membersHtml = render(members, { data: membersData, loading: false, error: null });
check("members: renders the roster", membersHtml.includes("Dana Rodriguez"));
check("members: formats the joined date", membersHtml.includes("Jan 14, 2026"));
check("members: renders the provisioning team", membersHtml.includes("acme/docs-team"));
states(members, buildMembers([], "", { connected: false, team: "" }, "none"));

/* ── Settings → API keys ────────────────────────────────────────────────── */

console.log("settings-api-keys");
const keyRows = mapApiKeys({ apiKeys: [
  { id: 1, name: "CI publisher", prefix: "mari_sk_abc…", scopes: "read,write", created: "2026-03-02", lastUsed: "2026-07-19", revoked: false },
  { id: 2, name: "Never used", prefix: "mari_sk_xyz…", scopes: "read", created: "2026-06-01", lastUsed: "", revoked: true },
] });
check("api keys: an unused key has no last-used date", keyRows[1].lastUsed === null);
const keysData = buildApiKeys(keyRows, "list");
check("api keys: rail counts active vs revoked",
  keysData.summary.find((s) => s.label === "Revoked")?.value === "1");
check("api keys: renders the list",
  render(apiKeys, { data: keysData, loading: false, error: null }).includes("CI publisher"));
states(apiKeys, buildApiKeys([], "list"));

/* ── Settings → Access log ──────────────────────────────────────────────── */

console.log("settings-audit-log");
const LOG_RES: any = {
  auditLog: [
    { id: 3, actor: "Dana Rodriguez", verb: "published", target: "help.mari.guru", at: "2026-07-20T14:57:00",
      detail: [{ label: "Release", value: "v14" }, { label: "Docs", value: "148" }] },
    { id: 2, actor: "Mari", verb: "verified a fact in", target: "Proration rule", at: "2026-07-20T09:12:00", detail: [] },
    { id: 1, actor: "Dana Rodriguez", verb: "invited", target: "priya@acme.com", at: "2026-07-19T16:40:00", detail: [] },
  ],
  auditLogTotal: 4211,
};
const logEvents = mapAuditLog(LOG_RES);
const logDetails = mapDetails(LOG_RES);
check("audit log: an event's detail comes back with it", logDetails.get(3)?.length === 2);
check("audit log: an event logged before detail existed has none", logDetails.get(2)?.length === 0);
const logData = buildAuditLog(logEvents, LOG_RES.auditLogTotal, logDetails, null);
check("audit log: the total is the whole log, not the window", logData.total === 4211);
check("audit log: counts distinct actors",
  logData.summary.find((s) => s.label === "Distinct actors")?.value === "2");
check("audit log: the rail says how deep the log goes",
  logData.summary.find((s) => s.label === "Events in log")?.value === "4,211");
check("audit log: oldest shown is the last row", logData.summary.find((s) => s.label === "Oldest shown")?.value === "2026-07-19T16:40:00");
const logHtml = render(auditLog, { data: logData, loading: false, error: null });
check("audit log: renders rows", logHtml.includes("help.mari.guru"));
check("audit log: formats the ISO timestamp", logHtml.includes("Jul 20, 2026"));
/* Detail is a property of the ROW now (`AuditEvent.detail`), and the page
   folds `data.detail` onto whichever row is expanded. So these assert the
   screen rather than the intermediate list: nothing expanded, nothing on
   screen; expand a row and that row's own values are there. */
/* Anchored on the <dt> the detail panel renders, not on a bare substring: an
   icon's SVG path data contains "v14" ("M12 7v14"), so a loose search reported
   a detail row that is not on screen. */
const hasDetail = (html: string, label: string) => html.includes(`>${label}</dt>`);
check("audit log: nothing expanded, no detail on screen",
  !hasDetail(logHtml, "Release") && !hasDetail(logHtml, "Docs"));
const expandedLog = buildAuditLog(logEvents, LOG_RES.auditLogTotal, logDetails, 3);
const expandedHtml = render(auditLog, { data: expandedLog, loading: false, error: null });
check("audit log: the expanded row renders its own detail",
  hasDetail(expandedHtml, "Release") && expandedHtml.includes("v14"));
/* Event 2 was logged before the writer recorded any detail. Expanding it must
   draw nothing — and in particular must not borrow another row's detail. */
const emptyDetailHtml = render(
  auditLog,
  { data: buildAuditLog(logEvents, LOG_RES.auditLogTotal, logDetails, 2), loading: false, error: null },
);
check("audit log: a row with no detail expands to nothing, not to another row's",
  emptyDetailHtml.includes("Proration rule")
  && !hasDetail(emptyDetailHtml, "Release") && !hasDetail(emptyDetailHtml, "Docs"));
states(auditLog, buildAuditLog([], 0, new Map(), null));

/* ── Doc review ─────────────────────────────────────────────────────────── */

console.log("doc-review");
const DOC_RES: any = {
  document: {
    id: 12, title: "Billing proration", author: "Dana Rodriguez", date: "2026-07-20",
    // The document's own tags and this reader's watch row. The header used to
    // draw "canonical"/"verified" on everything and a Watch button that knew
    // nothing; both are data now, so the response has to carry them.
    tags: ["canonical", "customer-facing"], watched: true,
    body: "## Overview\n\nGrowth tier prorates monthly.\n\n### Edge cases\n\nDowngrades settle next cycle."
      + "\n\n| Tier | Prorates |\n| --- | --- |\n| Growth | monthly |\n\n> Downgrades never refund.",
  },
  revisions: [{ id: 5, actor: "Dana Rodriguez", verb: "edited", at: "2026-07-20T14:57:00" }],
  findings: [
    { id: 1, kind: "fact", severity: "error", text: "Contradicts the accepted proration fact", note: "" },
    { id: 2, kind: "prose", severity: "warn", text: "Long sentence", note: "34 words" },
    { id: 3, kind: "prose", severity: "nit", text: "From a newer checker", note: "" },
  ],
  changes: [
    { id: 1, original: "leverage", replacement: "use", reason: "plain language", status: "pending" },
    { id: 2, original: "utilise", replacement: "use", reason: "plain language", status: "not_a_status" },
  ],
};
const docData = mapDocReview(DOC_RES);
check("doc review: the same body drives outline, editor and diff",
  docData.doc.outlineBody === docData.doc.editorBody && docData.doc.editorBody === docData.doc.changeBody);
check("doc review: severities tally into the refine panel",
  docData.doc.refine.errorN === 1 && docData.doc.refine.warnN === 1 && docData.doc.refine.advisoryN === 1);
check("doc review: an unknown change status reads as undecided",
  docData.doc.changes[1].state === "pending");
check("doc review: no document means the empty page, not a blank title",
  mapDocReview({ ...DOC_RES, document: null }).title === "");
check("doc review: the tags are the document's own, not two invented chips",
  docData.tags?.join() === "canonical,customer-facing");
const docHtml = render(docReview, { data: docData, loading: false, error: null });
check("doc review: renders the document", docHtml.includes("Billing proration"));
check("doc review: renders the document's tags", docHtml.includes("Canonical"));
/* `watched` seeds the Watch button; where a deployment does not know, the
   button is not drawn at all rather than claiming "Watch" at a watcher. */
check("doc review: a known watch state draws the control", docHtml.includes("Watching"));
check("doc review: an unknown watch state draws no control",
  !render(docReview, { data: { ...docData, watched: undefined }, loading: false, error: null })
    .includes("Watching"));
/* A table and a blockquote survive to the screen: the parser used to flatten
   both, which meant Save wrote back a document missing them. */
check("doc review: opaque markdown blocks survive to the page",
  docHtml.includes("Downgrades never refund"));
states(docReview, DOC_REVIEW_EMPTY);

/* ── Answers ────────────────────────────────────────────────────────────── */

console.log("answers");
const ANSWERS_RES: any = {
  approvedAnswers: [
    { id: 1, question: "How long do sessions last?", answer: "30-day rolling tokens.", status: "approved",
      owner: "Priya Nair", channels: ["slack-bot", "carrier-pigeon"], sources: [{ source: "docs", title: "Sessions" }],
      served: 1284, spark: [4, 6, 5], updated: "2026-07-16" },
    { id: 2, question: "Webhook retries?", answer: "Exponential backoff.", status: "draft",
      owner: "Marcus Vale", channels: [], sources: [], served: 0, spark: [], updated: "2026-07-15" },
    { id: 3, question: "From a newer status", answer: "…", status: "archived",
      owner: "", channels: [], sources: [], served: 0, spark: [], updated: "2026-07-01" },
  ],
  answerCoverageGaps: ["how do i rotate my api key?"],
  // Nothing indexed from chat, so that source has nothing to scan.
  answerHarvestSources: { slack: 412, docs: 1284, chat: 0 },
};
const answerRows = mapAnswers(ANSWERS_RES);
check("answers: a status this build cannot draw is dropped", answerRows.length === 2);
check("answers: an unknown channel has no toggle, so it is dropped",
  answerRows[0].channels.length === 1);
/* The harvest source list is data now: the page draws no "Harvest questions"
   button without it, and offers only the sources this workspace can scan. */
const harvestSources = mapHarvestSources(ANSWERS_RES);
check("answers: a source with nothing in it is not offered",
  harvestSources.length === 2 && harvestSources.every((s) => s.key !== "history"));
const answersData = buildAnswers(answerRows, ANSWERS_RES.answerCoverageGaps, "all", harvestSources);
check("answers: the stat strip counts the answers under it",
  answersData.stats[0].value === "1" && answersData.stats[2].value === "1,284");
const answersHtml = render(answers, { data: answersData, loading: false, error: null });
check("answers: renders the questions", answersHtml.includes("How long do sessions last?"));
check("answers: offers the harvest it has sources for", answersHtml.includes("Harvest questions"));
check("answers: a workspace with nothing to scan is offered no harvest",
  !render(answers, { data: buildAnswers(answerRows, [], "all"), loading: false, error: null })
    .includes("Harvest questions"));
states(answers, ANSWERS_EMPTY);

/* ── Lineage ────────────────────────────────────────────────────────────── */

console.log("lineage");
const LINEAGE_RES: any = {
  lineage: [
    { id: "doc:pricing", docId: 1, source: "notion", title: "Pricing FAQ", meta: "Dana · Jul 20", icon: "notion",
      x: 0.3, y: 0.4, pinned: false, date: "2026-07-20", createdDate: "2026-07-01", warn: false,
      owner: "Dana", tags: ["canonical"], staleDays: 4, orphan: false, inbound: 2, outbound: 1,
      docKind: "page", group: "" },
    { id: "gh:c1", docId: 2, source: "github", title: "Bump deps", meta: "CI · Jul 19", icon: "github",
      x: 0.5, y: 0.2, pinned: false, date: "2026-07-19", createdDate: "2026-07-19", warn: false,
      owner: "CI", tags: [], staleDays: 5, orphan: false, inbound: 0, outbound: 1,
      docKind: "commit", group: "gh:acme/docs:commits" },
    { id: "x:1", docId: 3, source: "docs", title: "From a newer classifier", meta: "", icon: "doc",
      x: 0.1, y: 0.1, pinned: false, date: "", createdDate: "", warn: false, owner: "", tags: [],
      staleDays: 0, orphan: true, inbound: 0, outbound: 0, docKind: "wormhole", group: "" },
  ],
  lineageEdges: [
    { id: 1, fromId: "gh:c1", toId: "doc:pricing", kind: "references", date: "2026-07-19", meta: { derived: "llm", confidence: 0.9 } },
    { id: 2, fromId: "gh:c1", toId: "doc:pricing", kind: "similar", date: "2026-07-19", meta: null },
    { id: 3, fromId: "gh:c1", toId: "gone", kind: "references", date: "2026-07-19", meta: null },
    { id: 4, fromId: "doc:pricing", toId: "gh:c1", kind: "from_a_newer_linker", date: "2026-07-19", meta: null },
  ],
  graphStats: { activity: [{ date: "2026-07-19", count: 3 }, { date: "2026-07-20", count: 5 }] },
  // Saved views: what `saveView` wrote, read back into the Views menu. Without
  // them the menu offers only the built-in presets and the write is unread.
  graphViews: [{ id: 4, name: "Canonical only", state: '{"status":"verified","lens":"source"}' }],
};
const lineageData = buildLineage(LINEAGE_RES);
check("lineage: a node kind the graph has no glyph for is dropped", lineageData.nodes.length === 2);
check("lineage: a relation outside the legend is dropped", lineageData.edges.length === 2);
check("lineage: `similar` is in the legend now, so it is drawn, not dropped",
  lineageData.edges.some((e) => e.rel === "similar"));
check("lineage: an edge to a node that is not there is dropped",
  lineageData.edges.every((e) => e.to !== "gone"));
check("lineage: machine-proposed edges are flagged", lineageData.edges[0].llm === true);
check("lineage: the scrubber snaps to the dates things happened on",
  lineageData.dates.join() === "2026-07-19,2026-07-20");
check("lineage: a saved view is read back, not just written",
  lineageData.views?.length === 1 && lineageData.views?.[0].name === "Canonical only");
const lineageOverview = buildOverviewGraph(lineageData.nodes, lineageData.edges);
check("lineage: overview rolls documents up before drawing",
  lineageOverview.nodes.length === 2 && lineageOverview.nodes.every((node) => node.macro));
check("lineage: overview omits similarity noise",
  lineageOverview.edges.every((edge) => edge.rel !== "similar"));
check("lineage: provenance follows dependent to source and ignores similarity",
  buildFocusedGraph(lineageData.nodes, lineageData.edges, "gh:c1", "provenance", 1).nodes.length === 2);
check("lineage: impact walks dependencies in reverse",
  buildFocusedGraph(lineageData.nodes, lineageData.edges, "doc:pricing", "impact", 1).nodes.some((node) => node.id === "gh:c1"));
/* The Views menu itself is a closed dropdown at render time (its content is
   unmounted until it is opened), so the assertion that it can list the view is
   that the view reached the page at all. */
check("lineage: renders the graph",
  render(lineage, { data: lineageData, loading: false, error: null }).includes("Notion · 1 document"));
states(lineage, LINEAGE_EMPTY);

/* ── Flows ──────────────────────────────────────────────────────────────── */

console.log("flows");
const FLOWS_RES: any = {
  workflows: [
    { id: 1, name: "Docs guardrail", description: "Fact-checks changed docs.", color: "#B23A1E", status: "active",
      nodes: [{ kind: "trigger", label: "PR merged" }, { kind: "fact_check", label: "Verify facts" }],
      trigger: { on: "document_changed", source_id: 1, path_glob: "docs/**" } },
    { id: 2, name: "Slack digest", description: "Weekly digest.", color: "#1E6FA8", status: "archived",
      nodes: [], trigger: { on: "schedule", every_minutes: 10080 } },
  ],
  workflowRuns: [
    { id: 900, workflowId: 1, workflowName: "Docs guardrail", number: 145, status: "passed",
      started: "2026-07-20T14:12:00", duration: "00:00:41", triggeredBy: "docs/api.md merged",
      stats: { ctx: { docs: [] }, contradictions: 2, edits: 0, links: 1 },
      rows: [{ step: "Verify facts", status: "passed", detail: "38 facts", duration: "38s" },
             { step: "From a newer engine", status: "quantum", detail: "" }] },
    { id: 899, workflowId: 1, workflowName: "Docs guardrail", number: 143, status: "exploded",
      started: "2026-07-19T10:02:00", duration: "", triggeredBy: "", stats: null, rows: null },
  ],
  sourcePulse: [{ id: 1, name: "GitHub · acme/docs" }],
};
const flowsData = buildFlows(FLOWS_RES);
check("flows: a run status outside the vocabulary is dropped", flowsData.flows[0].recentRuns.length === 1);
check("flows: a step status outside the vocabulary is dropped",
  flowsData.flows.length === 2 && FLOWS_RES.workflowRuns[0].rows.length === 2);
check("flows: a schedule reads as its interval", flowsData.flows[1].whenLabel === "Every week");
check("flows: a manual-only trigger says so", buildFlows({
  ...FLOWS_RES, workflows: [{ ...FLOWS_RES.workflows[0], trigger: {} }],
} as any).flows[0].whenLabel === "Manual only");
check("flows: only real per-run counters become tiles",
  flowsData.flows[0].lastRun?.status === "passed");
check("flows: anything but active is paused", flowsData.flows[1].status === "paused");
check("flows: renders the list",
  render(flows, { data: flowsData, loading: false, error: null }).includes("Docs guardrail"));
states(flows, FLOWS_EMPTY);

/* ── Library ────────────────────────────────────────────────────────────── */

console.log("library");
const LIBRARY_RES: any = {
  tagDefs: [
    { tag: "canonical", label: "Canonical", kind: "canonical", searchWeight: 2, isDefault: true,
      behaviors: "Boosts search; wins conflicts", usage: 142 },
    { tag: "runbook", label: "Runbook", kind: "from_a_newer_taxonomy", searchWeight: 1, isDefault: false,
      behaviors: "", usage: 0 },
  ],
  glossary: [{ id: 1, term: "Backfill", definition: "The initial full sync.", owner: "Priya", updated: "2026-07-14" }],
  graphStats: { docs: 420 },
  workspace: { name: "Northwind" },
  styleGuides: [
    { key: "plain", name: "Plain language", description: "Short words, active voice.", tone: "ok", builtin: true, rules: 12, preview: ["No hedging filler."] },
    { key: "house", name: "Northwind house style", description: "Ours.", tone: "from_a_newer_palette", builtin: false, rules: 3, preview: [] },
  ],
  defaultStylePack: "plain",
  voiceLayer: { voice: "Direct, never breezy.", terms: "workspace, not org", banned: "leverage", inclusive: true, jargon: false, sentenceCase: true },
  documentTemplates: [
    { key: "rfc", name: "RFC", category: "Engineering", description: "Propose a change.", sections: ["Problem", "Proposal"], icon: "git-fork", standard: true },
    { key: "odd", name: "From a newer gallery", category: "", description: "", sections: [], icon: "hyperbolic-manifold", standard: false },
  ],
  search: [
    { id: 1, title: "Payments runbook", source: "github", body: "It's worth noting that you guys should delve in." },
    { id: 2, title: "No body", source: "docs", body: "" },
  ],
};
const libraryData = buildLibrary(LIBRARY_RES, "tags");
check("library: behaviors split into chips", libraryData.tags[0].behaviors.length === 2);
check("library: an unmapped tag kind is neutral, not a guess", libraryData.tags[1].tone === "neutral");
check("library: a document with no body cannot be checked", libraryData.checkerDocs.length === 1);
check("library: the workspace name comes off the workspace record", libraryData.workspace === "Northwind");
check("library: the style packs are the stored ones", libraryData.guides.length === 2);
check("library: a pack's rule count is read off style_rules", libraryData.guides[0].rules === 12);
check("library: a tone this build has no colour for is not invented", libraryData.guides[1].tone === "ink");
check("library: the adopted pack is the stored key", libraryData.defaultPack === "plain");
check("library: the voice layer is the workspace's own", libraryData.voice.voice === "Direct, never breezy." && libraryData.voice.sentenceCase === true);
check("library: templates carry their sections", libraryData.templates[0].sections.join() === "Problem,Proposal");
check("library: a template icon this build has no glyph for reads as a document",
  libraryData.templates[1].icon === "file-text");
const libraryHtml = render(library, { data: libraryData, loading: false, error: null });
/* The tab badges are what the reader believes, and the page counts the very
   collections it is about to draw (`data.counts` is deprecated and unread).
   So the assertion reads the badge off the rendered strip rather than off a
   number nothing renders: a badge that says 40 over a panel drawing 12 is the
   exact bug the derivation fixed. */
const tabBadge = (html: string, label: string): number | null => {
  const m = new RegExp(`${label}<span[^>]*>(\\d+)</span>`).exec(html);
  return m ? Number(m[1]) : null;
};
check("library: the tab strip badges what the panels will draw",
  tabBadge(libraryHtml, "Tags") === libraryData.tags.length
  && tabBadge(libraryHtml, "Glossary") === libraryData.terms.length
  && tabBadge(libraryHtml, "Style guides") === libraryData.guides.length
  && tabBadge(libraryHtml, "Templates") === libraryData.templates.length);
check("library: those counts are the fixture's own collections",
  libraryData.tags.length === 2 && libraryData.terms.length === 1
  && libraryData.guides.length === 2 && libraryData.templates.length === 2);
check("library: the rules tab badges the checker's own registry",
  tabBadge(libraryHtml, "Rules") === RULE_COUNT && RULE_COUNT > 0);
check("library: renders the tag vocabulary", libraryHtml.includes("Canonical"));
check("library: renders the guides tab count", render(library, { data: { ...libraryData, tab: "guides" }, loading: false, error: null }).includes("Plain language"));
check("library: renders the templates gallery", render(library, { data: { ...libraryData, tab: "templates" }, loading: false, error: null }).includes("RFC"));
states(library, LIBRARY_EMPTY);

/* ── Publish ────────────────────────────────────────────────────────────── */

console.log("publish");
const PUBLISH_RES: any = {
  sites: [{ id: 1, name: "Acme Docs", domain: "docs.acme.com", status: "live",
    theme: { theme: "Mari Editorial", accent: "#b04e2c" }, sources: ["customer-facing", "canonical"],
    nav: [{ label: "Guides", docs: 74 }, { docs: 3 }],
    gates: [{ gate: "Fact check", status: "pass" }, { gate: "Glossary coverage", status: "fail" }],
    docs: 148, warnings: 2 }],
  releases: [
    { id: 9, siteId: 1, version: "v14", status: "live", deployed: "Jul 20", docs: 148, notes: "Deployed to S3" },
    { id: 8, siteId: 1, version: "v13", status: "previous", deployed: "Jul 13", docs: 140, notes: "" },
    { id: 7, siteId: 2, version: "v1", status: "live", deployed: "Jul 01", docs: 3, notes: "" },
  ],
  mcpServers: [
    { id: 1, name: "support-kb", url: "https://mcp/1", scope: "product", status: "connected", tools: 7,
      config: { capabilities: ["search", "facts"] } },
    { id: 2, name: "from-a-newer-build", url: "https://mcp/2", scope: "galaxy", status: "idle", tools: 1, config: null },
  ],
  siteThemePresets: [
    { key: "editorial", name: "Mari Editorial", accent: "#b04e2c", bg: "#faf7f2" },
    { key: "slate", name: "Slate", accent: "#3d5a80", bg: "#f4f6f8" },
  ],
  settings: [{ key: "deploy", value: { bucket: "acme-docs-prod", region: "us-east-1" } }],
  botsStatus: {
    slack: { configured: true, teamName: "Acme", lastEventAt: "2026-07-21T13:00:00", lastError: null },
    github: { webhookConfigured: false, lastDeliveryAt: null, sources: [{ id: 1, repo: "acme/handbook" }] },
  },
};
const PUBLISH_FEATURES: any[] = [
  { key: "search", label: "Search", hint: "Client-side index.", on: true },
  { key: "feedback", label: "Was this helpful?", hint: "Per-page rating.", on: false },
];
// `?site=1` is the editor route; the list is what the same response
// renders without one.
const publishData = buildPublish(PUBLISH_RES, 1, false, PUBLISH_FEATURES);
check("publish: the theme catalog is what the generator can render",
  publishData.site?.themes.length === 2 && publishData.site?.themes[0].accent === "#b04e2c");
check("publish: the feature switches carry the site's resolved state",
  publishData.site?.features.length === 2
  && publishData.site?.features[0].on === true && publishData.site?.features[1].on === false);
check("publish: features asked for by the routed site's id", pickSiteRow(PUBLISH_RES, 1)?.id === 1);
check("publish: no ?site= opens the list, not an editor", buildPublish(PUBLISH_RES).view === "site-list");
check("publish: the list carries every site", buildPublish(PUBLISH_RES).sites.length === 1);
check("publish: a site whose switches have not arrived shows none, not defaults",
  buildPublish(PUBLISH_RES, 1).site?.features.length === 0);
check("publish: a scope this build cannot label is dropped", publishData.servers.length === 1);
check("publish: releases are scoped to the site", publishData.site?.releases.length === 2);
check("publish: the version is the newest release", publishData.site?.version === "v14");
check("publish: a nav section with no label is dropped", publishData.site?.nav.length === 1);
check("publish: gates carry their real outcome",
  publishData.site?.gates[0].ok === true && publishData.site?.gates[1].ok === false);
check("publish: the deploy target comes off the settings row",
  publishData.site?.bucket === "acme-docs-prod");
check("publish: a live site is published, not a draft", publishData.phase === "published");
check("publish: bot destinations report the repositories their webhook covers",
  publishData.github.repos.join() === "acme/handbook");
check("publish: no sites means nothing to publish", buildPublish({
  sites: [], releases: [], mcpServers: [], siteThemePresets: [], settings: [],
} as any, 1).site === null);
const publishHtml = render(publish, { data: publishData, loading: false, error: null });
check("publish: renders the site", publishHtml.includes("docs.acme.com"));
// The preset list lives on the editor's Theme tab, not its Content tab.
check("publish: renders the theme catalog",
  render(publish, { data: { ...publishData, editorTab: "theme" }, loading: false, error: null }).includes("Slate"));
check("publish: renders the feature switches", publishHtml.includes("Was this helpful?"));
states(publish, PUBLISH_EMPTY);

/* ── Sources ────────────────────────────────────────────────────────────── */

console.log("sources");
const SOURCES_RES: any = {
  sourcePulse: [
    /* `syncIntervalMinutes` is only meaningful where a sync flow owns the
       source: without one there is no schedule to report, and the page then
       draws no schedule control for that row rather than a guessed value. */
    { id: 1, provider: "github", name: "acme/handbook", status: "active", docsCount: 1284,
      health: "Healthy", kind: "github", lastSyncAt: "2026-07-21T14:12:00", bars: [3, 5, 4],
      syncFlowId: 21, syncIntervalMinutes: 60 },
    { id: 2, provider: "confluence", name: "Confluence · Ops", status: "active", docsCount: 512,
      health: "Error", kind: "connector", lastSyncAt: "", bars: [],
      syncFlowId: 22, syncIntervalMinutes: null },
    { id: 3, provider: "docs", name: "Seeded docs", status: "active", docsCount: 12,
      health: "From a newer ingester", kind: "", lastSyncAt: "", bars: [],
      syncFlowId: null, syncIntervalMinutes: 15 },
  ],
  connectorCatalog: [
    { key: "github", name: "GitHub", blurb: "Markdown docs from repos.", docsUrl: "", connected: true, fields: [] },
    { key: "slack", name: "Slack", blurb: "Channel history.", connected: false,
      fields: [{ key: "bot_token", label: "Bot token", secret: true, placeholder: "xoxb-…", help: "" }] },
  ],
  botsStatus: {
    slack: { configured: true, teamName: "Acme", lastEventAt: "2026-07-21T13:00:00", lastError: null },
    github: { webhookConfigured: false, lastDeliveryAt: null, sources: [{ id: 1, repo: "acme/handbook" }] },
  },
};
const sourcesData = buildSources(SOURCES_RES);
check("sources: a connector-owned source can report live sync state",
  sourcesData.sources[0].tier === "live" && sourcesData.sources[2].tier === "legacy");
check("sources: a failing source says so", sourcesData.sources[1].state === "failed");
check("sources: a health word this build does not know is not a failure",
  sourcesData.sources[2].state === "healthy");
check("sources: a source that never synced has no last-sync date",
  sourcesData.sources[1].lastSyncAt === null);
check("sources: a schedule is reported only where a sync flow owns the source",
  sourcesData.sources[0].syncIntervalMinutes === 60
  && sourcesData.sources[1].syncIntervalMinutes === null
  && sourcesData.sources[2].syncIntervalMinutes === undefined);
check("sources: the catalog carries specs, never values",
  sourcesData.catalog[1].fields[0].secret === true);
check("sources: rail counts come off the grid",
  sourcesData.summary.find((s) => s.label === "Failing")?.value === "1");
check("sources: renders the grid",
  render(sources, { data: sourcesData, loading: false, error: null }).includes("acme/handbook"));
states(sources, SOURCES_EMPTY);

/* ── Settings → General ─────────────────────────────────────────────────── */

console.log("settings-general");
const GENERAL_RES: any = {
  workspace: { name: "Acme Data Platform", slug: "acme-data", plan: "team", timezone: "America/Los_Angeles", language: "English (US)" },
  provisioning: {
    ssoProviders: ["github"], ssoEnabled: true, scimStatus: "unavailable",
    githubTeam: { team: "acme/docs-team", connected: true, syncedMembers: 6 },
  },
  members: [{ id: 1, status: "active" }, { id: 2, status: "invited" }],
  graphStats: { docs: 420 },
};
const generalData = buildSettingsGeneral(GENERAL_RES);
check("settings-general: the record is the workspace row", generalData.slug === "acme-data");
check("settings-general: the rail reports provisioning as configured",
  generalData.summary.find((s) => s.label === "GitHub team")?.value === "acme/docs-team · 6 synced");
check("settings-general: SSO is listed only when a credential exists",
  generalData.summary.find((s) => s.label === "Single sign-on")?.value === "github");
check("settings-general: a team nobody configured is not a row",
  buildSettingsGeneral({ ...GENERAL_RES, provisioning: { ssoProviders: [], ssoEnabled: false, scimStatus: "unavailable", githubTeam: { team: "", connected: false, syncedMembers: 0 } } } as any)
    .summary.find((s) => s.label === "GitHub team") === undefined);
check("settings-general: a freshly loaded form is clean and unrejected",
  generalData.save === "clean" && generalData.slugError === null);
check("settings-general: rail counts come off the roster",
  generalData.summary.find((s) => s.label === "Members")?.value === "1 active, 1 invited");
check("settings-general: a fresh workspace has nothing to state",
  buildSettingsGeneral({ workspace: null, provisioning: null, members: [], graphStats: null } as any).summary.length === 0);
check("settings-general: renders the workspace",
  render(settingsGeneral, { data: generalData, loading: false, error: null }).includes("Acme Data Platform"));
states(settingsGeneral, GENERAL_EMPTY);

/* ── Settings → Models ──────────────────────────────────────────────────── */

console.log("settings-models");
const MODELS_RES: any = {
  settings: [
    { key: "embedding", value: { provider: "openai", model: "text-embedding-3-small", dims: 1536, options: ["openai:text-embedding-3-small", "ollama:nomic-embed-text"] } },
    { key: "llm", value: { provider: "anthropic", model: "claude-sonnet-5", options: ["anthropic:claude-sonnet-5"], keys: { anthropic: "••••…3f2a", openai: "" } } },
    { key: "chunking", value: { default: { strategy: "heading", max_tokens: 512, overlap: 64 }, slack: { strategy: "thread", max_tokens: 768, overlap: 0 } } },
  ],
  indexStats: { docs: 12480, chunks: 90210, embedded: 90000 },
  sourcePulse: [{ provider: "slack", name: "Slack · #engineering" }],
};
const modelsData = buildSettingsModels(MODELS_RES);
check("settings-models: the dropdown value is provider:model",
  modelsData.embedding === "openai:text-embedding-3-small");
check("settings-models: chunking names the source the way the console does",
  modelsData.chunking.some((c) => c.source === "Slack · #engineering"));
check("settings-models: the default row is named, not blank",
  modelsData.chunking.some((c) => c.source === "Default"));
check("settings-models: an unset provider key is empty, not a mask", modelsData.keys.openai === "");
check("settings-models: the corpus line is counted", modelsData.indexSummary.includes("12,480 documents"));
check("settings-models: nothing has been tested on load",
  modelsData.testOk === "" && modelsData.testError === "");
check("settings-models: a workspace with no models chosen shows none",
  buildSettingsModels({ settings: [], indexStats: null, sourcePulse: [] } as any).embedding === "");
check("settings-models: renders the configuration",
  render(settingsModels, { data: modelsData, loading: false, error: null }).includes("text-embedding-3-small"));
states(settingsModels, MODELS_EMPTY);

/* ── Welcome ────────────────────────────────────────────────────────────── */

console.log("welcome");
const WELCOME_RES: any = {
  connectorCatalog: [
    { key: "github", name: "GitHub", blurb: "Markdown docs from repos.", connected: true, fields: [] },
    { key: "slack", name: "Slack", blurb: "Channel history.", connected: false,
      fields: [{ key: "bot_token", label: "Bot token", secret: true, placeholder: "xoxb-…", help: "channels:history" }] },
  ],
  githubRepos: [{ fullName: "acme/handbook", description: "Handbook", private: true, defaultBranch: "main", connected: false }],
  glossaryCandidates: [
    { id: 1, term: "Backfill", variants: "back-fill", definition: "The initial full sync.", evidence: "Ingest runbook" },
    { id: 2, term: "Typed by hand", variants: "", definition: "No source document.", evidence: "" },
  ],
  sourcePulse: [
    { id: 1, provider: "github", name: "GitHub · acme/handbook", status: "active", docsCount: 1284, health: "Healthy", kind: "github", lastSyncAt: "2026-07-21T14:12:00" },
    { id: 2, provider: "confluence", name: "Confluence · Ops", status: "active", docsCount: 0, health: "Error", kind: "connector", lastSyncAt: "" },
  ],
  uploadManifest: {
    summary: "2 files · 41 chunks · 41 embedded",
    files: [
      { name: "handbook.pdf", detail: "29 chunks · 29 embedded" },
      { name: "pricing.md", detail: "12 chunks · 12 embedded" },
    ],
  },
  styleGuides: [
    { key: "plain", name: "Plain language", description: "Short words, active voice.", rules: 12 },
    { key: "house", name: "Northwind house style", description: "Ours.", rules: 3 },
  ],
  defaultStylePack: "plain",
};
const welcomeData = buildWelcome(WELCOME_RES);
check("welcome: the style packs are the stored ones, with real rule counts",
  welcomeData.packs.length === 2 && welcomeData.packs[0].rules === 12);
check("welcome: the closing line names the pack that was adopted",
  welcomeData.doneSummary.guide === "Plain language");
check("welcome: an adopted key nothing defines any more names no pack",
  buildWelcome({ ...WELCOME_RES, defaultStylePack: "gone" }).doneSummary.guide === "");
check("welcome: the upload receipt is the manifest, counted off chunks",
  welcomeData.uploadFiles.length === 2 && welcomeData.uploadFiles[0].detail === "29 chunks · 29 embedded");
check("welcome: the upload summary is the server's own line",
  welcomeData.uploadSummary === "2 files · 41 chunks · 41 embedded");
check("welcome: a workspace that uploaded nothing gets no receipt",
  buildWelcome({ ...WELCOME_RES, uploadManifest: { summary: "", files: [] } }).uploadSummary === "");
check("welcome: a mined candidate cites the document it came from",
  welcomeData.glossaryCandidates[0].evidence === "Ingest runbook");
check("welcome: a hand-typed candidate cites nothing",
  welcomeData.glossaryCandidates[1].evidence === "");
check("welcome: the tiles are the catalog", welcomeData.connectorCount === 2 && welcomeData.tiles[0].connected === true);
check("welcome: credential fields are specs, with no value",
  welcomeData.slackFields[0].secret === true && welcomeData.slackFields[0].value === undefined);
check("welcome: repos come off the token's scope", welcomeData.repos[0].name === "acme/handbook");
check("welcome: a failing source is not reported as synced",
  welcomeData.syncRows[1].state === "error");
check("welcome: the closing line counts the table above it",
  welcomeData.doneSummary.sourcesSynced === 1 && welcomeData.doneSummary.glossaryTerms === 2);
check("welcome: renders the wizard",
  render(welcome, { data: welcomeData, loading: false, error: null }).length > 500);
states(welcome, WELCOME_EMPTY);

/* ── Login / Setup ──────────────────────────────────────────────────────────
   Neither reads GraphQL: what they render comes from /auth/me, which the auth
   context already fetches. Both are still adapted pages and must survive their
   universal states like every other. */

console.log("login");
const loginData = buildLogin({ github: true, google: false }, "credentials", false);
check("login: only providers with credentials are offered", loginData.providers.join() === "github");
check("login: a server with no OAuth configured offers none",
  buildLogin({}, "credentials", false).providers.length === 0);
check("login: renders the form",
  render(login, { data: loginData, loading: false, error: null }).length > 500);
states(login, buildLogin({}, "credentials", false));

console.log("setup");
check("setup: an unclaimed install opens on the token step", buildSetup(true).step === "token");
check("setup: a claimed install is done", buildSetup(false).step === "done");
check("setup: renders the token step",
  render(setup, { data: buildSetup(true), loading: false, error: null }).length > 500);
states(setup, buildSetup(false), { errorIgnored: true });

/* ── result ─────────────────────────────────────────────────────────────── */

if (failures) { console.error(`\n${failures} smoke failure(s).`); process.exit(1); }
console.log("\nsmoke OK");
