import type { Page, Route } from "@playwright/test";

export const USER = {
  id: 1, name: "Dana Rodriguez", email: "dana@example.test", role: "admin",
  initials: "DR", tint: 2, provider: "password",
};

type Call = { query: string; variables: Record<string, unknown> };

export type MockApi = {
  calls: Call[];
  restCalls: { path: string; body: any }[];
  failNext: (pattern: RegExp, message: string) => void;
  failNextAuthCheck: (status?: number) => void;
  setData: (key: string, value: any) => void;
};

const now = "2026-08-19T12:00:00Z";

function initialData() {
  return {
    overviewStats: { changes: 12, factsReview: 1, flowsRunning: 0, documents: 3 },
    tasks: [{
      id: 1, title: "Verify retention policy", assigneeInitials: "DR", kind: "factcheck",
      kindLabel: "Fact check", done: false, due: "2026-08-25", overdue: false,
      subjectType: "document", subjectId: "1", subjectTitle: "Retention runbook",
      subjectHref: "/knowledge/doc?id=1",
    }],
    reviewItems: {
      items: [
        { id: "task:1", kind: "task", title: "Verify retention policy", status: "pending", source: "github", assignee: "Dana Rodriguez", due: "2026-08-25", subjectType: "document", subjectId: "1", subjectTitle: "Retention runbook", subjectHref: "/knowledge/doc?id=1", confidence: 0, evidenceCount: 0, trustedSource: false },
        { id: "fact:2", kind: "fact", title: "Retention is 10 days", status: "pending", source: "Old handbook", assignee: "Dana Rodriguez", due: "", subjectType: "fact", subjectId: "2", subjectTitle: "Retention is 10 days", subjectHref: "/facts?fact=2", confidence: 0.71, evidenceCount: 1, trustedSource: false },
        { id: "decision:3", kind: "decision", title: "Move derived vectors to object storage", status: "pending", source: "ADR draft", assignee: "Lee Chen", due: "", subjectType: "decision", subjectId: "3", subjectTitle: "Object storage decision", subjectHref: "/decisions?decision=3", confidence: 0.95, evidenceCount: 3, trustedSource: true },
        { id: "answer:4", kind: "answer", title: "What is the deletion SLA?", status: "pending", source: "support", assignee: "Dana Rodriguez", due: "", subjectType: "answer", subjectId: "4", subjectTitle: "Deletion SLA answer", subjectHref: "/answers?answer=4", confidence: 0.92, evidenceCount: 2, trustedSource: true },
        { id: "finding:5", kind: "finding", title: "Conflicting retention duration", status: "pending", source: "github", assignee: "", due: "", subjectType: "document", subjectId: "1", subjectTitle: "Retention runbook", subjectHref: "/knowledge/doc?id=1&pane=findings", confidence: 0, evidenceCount: 1, trustedSource: false },
        { id: "change:6", kind: "change", title: "Replace 10 days with 30 days", status: "pending", source: "github", assignee: "", due: "", subjectType: "document", subjectId: "1", subjectTitle: "Retention runbook", subjectHref: "/knowledge/doc?id=1&tab=changes", confidence: 1, evidenceCount: 1, trustedSource: false },
        { id: "workflow:7", kind: "workflow", title: "Fact review approval", status: "waiting", source: "automation", assignee: "Dana Rodriguez", due: "", subjectType: "workflow", subjectId: "1", subjectTitle: "Fact review", subjectHref: "/flows?run=1", confidence: 1, evidenceCount: 1, trustedSource: true },
      ],
      totalCount: 7,
      pageInfo: { endCursor: "Nw", hasNextPage: false },
    },
    tasksSummary: { title: "Review queue", tags: ["Fact check"], people: ["DR"], statValue: "1", statLabel: "open" },
    digest: [{ title: "Policy updated", summary: "Retention documentation changed.", where: [{ source: "github", label: "Retention runbook" }], impact: [{ name: "Support", tone: "info" }] }],
    activityFeed: [{ id: 1, kind: "run", actor: "Mari", text: "synced", target: "Retention runbook", secondsAgo: 30 }],
    search: [{ id: 1, source: "github", title: "Retention runbook", snippet: "Retention is 30 days.", body: "# Retention\nRetention is 30 days.", kind: "page", author: "Dana", authorInitials: "DR", date: now, tags: ["canonical"] }],
    searchTotal: 1,
    sourcePulse: [
      { id: 1, provider: "github", name: "acme/handbook", status: "active", stat: "1", unit: "docs", docsCount: 1, health: "Healthy", kind: "github", lastSyncAt: now, bars: [1, 2, 1], syncIntervalMinutes: 60, syncFlowId: 11 },
      { id: 2, provider: "confluence", name: "Confluence — ENG", status: "active", stat: "1", unit: "docs", docsCount: 1, health: "Healthy", kind: "connector", lastSyncAt: now, bars: [1], syncIntervalMinutes: 60, syncFlowId: 12 },
    ],
    workflows: [{ id: 1, name: "Fact review", description: "Scan and approve facts", color: "#5c7a4c", pinned: true, status: "active", trigger: { on: "schedule", every_minutes: 60 }, nodes: [
      { kind: "trigger", label: "Every hour", config: {} },
      { kind: "scan_facts", label: "Scan facts", config: {} },
      { kind: "approval", label: "Approve", config: { assignee: "Dana" } },
    ] }],
    workflowRuns: [{ id: 1, workflowId: 1, workflowName: "Fact review", number: 1801, status: "waiting", started: now, startedLabel: "Aug 19", duration: "00:00:04", progress: 67, stats: { facts: 1, paused_at: 2 }, rows: [
      { step: "Every hour", status: "passed", detail: "scheduled", duration: "00:00:00" },
      { step: "Scan facts", status: "passed", detail: "1 claim", duration: "00:00:03" },
      { step: "Approve", status: "waiting", detail: "awaiting Dana", duration: "00:00:00" },
    ] }],
    facts: [
      { id: 1, claim: "Retention is 30 days.", source: "Retention runbook", owner: "Dana", status: "Verified", verified: "2026-08-18" },
      { id: 2, claim: "Retention is 10 days.", source: "Old handbook", owner: "Dana", status: "Needs review", verified: "" },
    ],
    factContradictions: [{ factId: 1, claim: "Retention is 30 days.", otherFactId: 2, otherClaim: "Retention is 10 days.", reason: "numeric conflict", detail: "30 versus 10 days" }],
    decisions: [{ id: 1, statement: "Use Postgres for metadata", context: "Queryable state stays relational.", status: "ratified", sourceLabel: "GitHub · ADR-14", owners: ["Dana"], decidedOn: "2026-08-18", supersededBy: null, supersededByStatement: "", impactSummary: "Affects storage design", impactCount: 2 }],
    widgets: { stats: [{ value: "1", label: "finding", sub: "this week", tone: "attention" }], reads: [], glossary: [], translation: [], localization: [], monitors: [] },
    freshness: [{ source: "GitHub", age: "1h", status: "fresh", detail: "polled" }],
    auditRuns: [], auditFindings: [], auditChecks: [], auditSummary: null,
    events: [{ id: 1, actor: "Mari", verb: "synced source", target: "acme/handbook", at: now, kind: "sync", detail: [] }],
    auditActors: ["Mari", "Dana Rodriguez"], auditActions: ["synced source"],
    members: [{ id: 1, name: "Dana Rodriguez", email: "dana@example.test", role: "admin", initials: "DR", tint: 2, status: "active", joined: "2026-08-01", lastActive: now, provider: "password" }],
    githubTeam: { org: "acme", slug: "docs", connected: true, syncedMembers: 1 },
    apiKeys: [{ id: 1, name: "CI", prefix: "mari_abcd", scopes: ["read"], created: "2026-08-01", lastUsed: now, revoked: false }],
    documents: [{ id: 1, source: "github", title: "Retention runbook", snippet: "Retention is 30 days.", body: "# Retention\nRetention is 30 days.", kind: "page", author: "Dana", date: now, tags: ["canonical"], watched: true, externalId: "github:1" }],
    document: { id: 1, source: "github", title: "Retention runbook", snippet: "Retention is 30 days.", body: "# Retention\nRetention is 30 days.", kind: "page", author: "Dana", date: now, tags: ["canonical"], watched: true },
    revisions: [{ id: 1, actor: "Dana", verb: "updated", at: now }],
    relatedDocuments: [], findings: [{ id: 1, kind: "fact", severity: "error", text: "Retention is 10 days", note: "Conflicts with verified fact" }],
    changes: [{ id: 1, original: "10 days", replacement: "30 days", reason: "Verified policy", status: "pending" }],
    claims: [{ id: 1, claim: "Retention is 30 days.", source: "Retention runbook", status: "Verified", verified: "2026-08-18" }],
    approvedAnswers: [{ id: 1, question: "How long is retention?", answer: "Retention is 30 days.", status: "approved", owner: "Dana", channels: ["slack-bot"], sources: [{ source: "github", title: "Retention runbook" }], served: 4, spark: [1, 2, 1], updated: now }],
    answerCoverageGaps: ["What is the deletion SLA?"], answerHarvestSources: { slack: 2, docs: 3, chat: 1 },
    lineage: [{ id: "github:1", docId: 1, title: "Retention runbook", source: "github", docKind: "page", icon: "file", x: 0.5, y: 0.5, pinned: false, date: "2026-08-19", createdDate: "2026-08-18", warn: false, owner: "Dana", tags: ["canonical"], staleDays: 0, orphan: true, inbound: 0, outbound: 0, group: "", meta: "Canonical policy" }],
    lineageEdges: [], graphStats: { docs: 3, edges: 0, sources: 2, people: 1 }, graphViews: [],
    tagDefs: [{ tag: "canonical", label: "Canonical", kind: "canonical", searchWeight: 2, isDefault: true, behaviors: "Boosts search", usage: 1 }],
    glossary: [{ id: 1, term: "Retention", definition: "How long data is kept.", owner: "Dana", updated: now }],
    workspace: { name: "Acme", slug: "acme", plan: "team", timezone: "America/Los_Angeles", language: "English (US)" },
    styleGuides: [{ key: "plain", name: "Plain language", description: "Direct writing.", tone: "ok", builtin: true, rules: 2, preview: ["Use active voice"] }],
    defaultStylePack: "plain", voiceLayer: { voice: "Direct", terms: "workspace", banned: "", inclusive: true, jargon: false, sentenceCase: true },
    documentTemplates: [{ key: "runbook", name: "Runbook", category: "Operations", description: "Operational procedure", sections: ["Purpose", "Steps"], icon: "clipboard", standard: true }],
    sites: [{ id: 1, name: "Acme Docs", domain: "docs.example.test", status: "live", theme: { theme: "Mari Editorial", accent: "#b04e2c" }, sources: ["canonical"], nav: [{ label: "Guides", docs: 1 }], gates: [{ gate: "Fact check", status: "pass" }], docs: 1, warnings: 0 }],
    releases: [{ id: 1, siteId: 1, version: "v1.0.0", status: "live", deployed: "Aug 19", docs: 1, notes: "Published" }],
    mcpServers: [{ id: 1, name: "support-kb", url: "http://localhost:8000/mcp/support-kb", scope: "workspace", status: "connected", tools: 2, config: { capabilities: ["search", "facts"] } }],
    knowledgeChatDestinations: [],
    siteThemePresets: [{ key: "editorial", name: "Mari Editorial", accent: "#b04e2c", bg: "#faf7f2" }],
    siteFeatures: [{ key: "search", label: "Search", hint: "Client-side index", on: true }],
    settings: [
      { key: "deploy", value: { bucket: "acme-docs", region: "us-west-2" } },
      { key: "embedding", value: { provider: "ollama", model: "nomic-embed-text", dims: 768, options: ["ollama:nomic-embed-text", "ollama:mxbai-embed-large"] } },
      { key: "llm", value: { provider: "ollama", model: "gemma3:4b", options: ["ollama:gemma3:4b", "ollama:llama3.2"], keys: {}, gateway: { base_url: "https://gateway.example.test/v1", token: "••••…oken", headers: { "X-Tenant": "acme" }, metadata: { application: "mari" }, model_header: "X-Model-ID", max_retries: 2 } } },
      { key: "chunking", value: { default: { strategy: "heading", max_tokens: 512, overlap: 64 } } },
      { key: "branding", value: { name: "Acme", primary: "#b04e2c" } },
    ],
    indexStats: { docs: 3, chunks: 4, embedded: 4 },
    modelCatalog: {
      embedding: ["ollama:nomic-embed-text", "ollama:mxbai-embed-large", "sentence-transformers:sentence-transformers/all-mpnet-base-v2"],
      generation: ["ollama:gemma3:4b", "ollama:llama3.2", "gateway:deepseek-v4-flash"],
      errors: {},
    },
    connectorCatalog: [
      { key: "github", name: "GitHub", blurb: "Repositories", connected: true, fields: [{ key: "token", label: "Personal access token", secret: true, required: true }, { key: "repo", label: "Repository", required: true }] },
      { key: "slack", name: "Slack", blurb: "Channel history", connected: false, fields: [{ key: "bot_token", label: "Bot token", secret: true, required: true }, { key: "channels", label: "Channels", required: false }] },
      { key: "gdrive", name: "Google Drive", blurb: "Google Docs and text files", connected: false, fields: [{ key: "access_token", label: "OAuth2 access token", secret: true, required: true }, { key: "folder_id", label: "Folder ID", required: false }] },
      { key: "confluence", name: "Confluence", blurb: "Wiki pages", connected: true, fields: [{ key: "site_url", label: "Site URL", required: true }, { key: "email", label: "Atlassian account email", required: true }, { key: "api_token", label: "API token", secret: true, required: true }] },
    ],
    botsStatus: { slack: { configured: true, teamName: "Acme", lastEventAt: now, lastError: null }, github: { webhookConfigured: true, lastDeliveryAt: now, sources: [{ id: 1, repo: "acme/handbook" }] } },
    githubRepos: [{ fullName: "acme/handbook", description: "Handbook", private: true, defaultBranch: "main", connected: true }],
    glossaryCandidates: [], uploadManifest: { summary: "", files: [] },
    provisioning: { ssoProviders: ["github"], ssoEnabled: true, scimStatus: "unavailable", githubTeam: { team: "acme/docs", org: "acme", slug: "docs", connected: true, syncedMembers: 1 } },
    notifications: [{ id: 1, kind: "info", text: "Sync complete", detail: "acme/handbook", at: now, read: false }],
    recentSearches: ["retention"],
    trajectories: [{
      id: 1, sessionId: 10, prompt: "Update the retention documentation", status: "ready",
      model: "ollama:gemma3:4b", layer1: "Searched the knowledge base, inspected the runbook, and updated the document.",
      layer2: "Updated a policy document from retrieved evidence.", category: "Documentation maintenance",
      macroIntent: "Repair policy documentation",
      phases: [
        { id: 0, name: "Discover", family: "discover", start: 0, end: 0, steps: 1, substate: "Progress", failures: 0 },
        { id: 1, name: "Inspect", family: "inspect", start: 1, end: 1, steps: 1, substate: "Progress", failures: 0 },
        { id: 2, name: "Change", family: "change", start: 2, end: 2, steps: 1, substate: "Progress", failures: 0 },
      ],
      stepCount: 3, failureCount: 0, reworkCount: 0, startedAt: now, completedAt: now,
      steps: [
        { ordinal: 0, tool: "search", actionFamily: "discover", args: { query: "retention" }, summary: "3 hits", ok: true },
        { ordinal: 1, tool: "read_document", actionFamily: "inspect", args: { id: 1 }, summary: "read Retention runbook", ok: true },
        { ordinal: 2, tool: "create_task", actionFamily: "change", args: { kind: "review" }, summary: "opened review for Retention runbook", ok: true },
      ],
    }],
    trajectoryTotal: 1,
    trajectoryCategories: ["Documentation maintenance"],
  } as Record<string, any>;
}

export async function installMockApi(page: Page, options: {
  signedIn?: boolean; needsSetup?: boolean;
  projects?: { id: number; slug: string; name: string; role: string; capabilities: string[] }[];
} = {}): Promise<MockApi> {
  const state = initialData();
  const calls: Call[] = [];
  const restCalls: { path: string; body: any }[] = [];
  let signedIn = options.signedIn ?? true;
  let needsSetup = Boolean(options.needsSetup);
  let failure: { pattern: RegExp; message: string } | null = null;
  let authFailureStatus: number | null = null;

  const projects = options.projects ?? [{ id: 1, slug: "default", name: "Mari", role: "admin", capabilities: ["knowledge.read"] }];
  await page.route("**/auth/me", (route) => {
    if (authFailureStatus !== null) {
      const status = authFailureStatus;
      authFailureStatus = null;
      return route.fulfill({ status, json: { detail: "temporary auth outage" } });
    }
    const requested = route.request().headers()["x-mari-project"];
    const activeProject = projects.find((project) => String(project.id) === requested || project.slug === requested)
      ?? (projects.length === 1 ? projects[0] : null);
    return route.fulfill({ json: {
      user: signedIn ? USER : null, needsSetup, bypassEnabled: false, registrationEnabled: false,
      oauth: { github: true, google: true }, projects, activeProject,
      capabilities: activeProject?.capabilities ?? [],
    } });
  });
  await page.route("**/auth/logout", (route) => { signedIn = false; return route.fulfill({ json: { ok: true } }); });
  await page.route("**/auth/setup", async (route) => {
    const body = route.request().postDataJSON();
    restCalls.push({ path: "/auth/setup", body });
    signedIn = true;
    needsSetup = false;
    return route.fulfill({ json: { user: USER } });
  });
  await page.route("**/auth/preferences", (route) => route.fulfill({ json: {
    name: USER.name,
    email: USER.email,
    initials: "MT",
    role: "admin",
    joined: "2025-01-15T00:00:00Z",
    provider: "password",
    timezone: "America/Los_Angeles",
    notifications: { mentions: true, digest: true, flowFailures: true },
  } }));
  await page.route("**/graphql", async (route: Route) => {
    const req = route.request();
    const body = req.postDataJSON() as { query?: string; variables?: Record<string, unknown> };
    const query = body.query || "";
    const variables = body.variables || {};
    calls.push({ query, variables });
    if (failure?.pattern.test(query)) {
      const message = failure.message;
      failure = null;
      return route.fulfill({ json: { errors: [{ message }] } });
    }
    let data: Record<string, any> = { ...state };
    if (/query Trajectories/.test(query)) {
      const category = String(variables.category || "");
      const matching = category ? state.trajectories.filter((row: any) => row.category === category) : state.trajectories;
      const offset = Number(variables.offset || 0);
      const limit = Number(variables.limit || 25);
      data = { trajectories: matching.slice(offset, offset + limit), trajectoryTotal: matching.length,
        trajectoryCategories: state.trajectoryCategories };
    } else if (/verifyFact/.test(query)) {
      const fact = state.facts.find((f: any) => f.id === variables.id);
      if (fact) { fact.status = "Verified"; fact.verified = "2026-08-19"; }
      data = { verifyFact: true };
    } else if (/addFact/.test(query)) {
      state.facts.push({ id: state.facts.length + 1, claim: variables.claim, source: variables.source, owner: variables.owner, status: "Needs review", verified: "" });
      data = { addFact: true };
    } else if (/startFactScan/.test(query)) {
      data = { startFactScan: 99 };
    } else if (/workflowRun\(/.test(query)) {
      data = { workflowRun: { id: 99, number: 1900, workflowName: "Fact scan", status: "passed", progress: 100, stats: { facts: 2 }, rows: [{ step: "Scan facts", status: "passed", detail: "2 claims", duration: "00:00:01" }] } };
    } else if (/approveRun/.test(query)) {
      data = { approveRun: true };
    } else if (/runWorkflow/.test(query)) {
      data = { runWorkflow: 1901 };
    } else if (/saveWorkflow/.test(query)) {
      data = { saveWorkflow: Number(variables.id) || 2 };
    } else if (/setWorkflow(?:Status|Trigger)/.test(query)) {
      data = { setWorkflowStatus: true, setWorkflowTrigger: true };
    } else if (/createMcpServer/.test(query)) {
      state.mcpServers.push({ id: 2, name: variables.name, url: `http://localhost:8000/mcp/${String(variables.name).toLowerCase().replace(/\W+/g, "-")}`, scope: variables.scope, status: "connected", tools: (variables.capabilities as any[])?.length || 1, config: { capabilities: variables.capabilities } });
      data = { createMcpServer: "mari_mcp_browser_test" };
    } else if (/testMcpServer/.test(query)) {
      data = { testMcpServer: { ok: true, latency_ms: 7, checks: { search: 1, facts: 2 } } };
    } else if (/createKnowledgeChatDestination/.test(query)) {
      const id = 7;
      state.knowledgeChatDestinations.push({ id, name: variables.name, slug: variables.slug, title: variables.title,
        welcome: variables.welcome, status: "draft", url: `/knowledge-chat/default/${variables.slug}` });
      data = { createKnowledgeChatDestination: id };
    } else if (/updateKnowledgeChatDestination/.test(query)) {
      const chat = state.knowledgeChatDestinations.find((row: any) => row.id === variables.id);
      if (chat) Object.assign(chat, { name: variables.name, title: variables.title, welcome: variables.welcome });
      data = { updateKnowledgeChatDestination: true };
    } else if (/deployKnowledgeChatDestination/.test(query)) {
      const chat = state.knowledgeChatDestinations.find((row: any) => row.id === variables.id);
      if (chat) chat.status = "live";
      data = { deployKnowledgeChatDestination: chat?.url ?? "" };
    } else if (/deleteMcpServer/.test(query)) {
      data = { deleteMcpServer: true };
    } else if (/updateMcpServer/.test(query)) {
      data = { updateMcpServer: true };
    } else if (/syncSource|resyncSource/.test(query)) {
      data = { syncSource: true, resyncSource: true };
    } else if (/testLlmGateway/.test(query)) {
      data = { testLlmGateway: { ok: true, detail: "LLM gateway is reachable and authenticated", models: 4, latency_ms: 12 } };
    } else if (/updateSetting/.test(query)) {
      const existing = state.settings.find((row: any) => row.key === variables.key);
      if (existing) existing.value = variables.value;
      else state.settings.push({ key: variables.key, value: variables.value });
      data = { updateSetting: true };
    } else if (/createTask/.test(query)) {
      data = { createTask: true };
    } else if (/evaluateReviewItem/.test(query)) {
      data = { evaluateReviewItem: { reviewId: variables.reviewId, outcome: "manual",
        explanation: "More evidence is required.", replayed: false, dryRun: variables.dryRun } };
    } else if (/createApiKey/.test(query)) {
      data = { createApiKey: "mari_browser_secret_once" };
    } else if (/revokeApiKey/.test(query)) {
      data = { revokeApiKey: true };
    } else if (/importBrand/.test(query)) {
      data = { importBrand: { title: "Acme", themeColor: "#5c7a4c", cssColors: [["#5c7a4c", 8]], fonts: ["Inter"], logo: null, warnings: [] } };
    } else if (/scanAnswerCandidates/.test(query)) {
      data = { scanAnswerCandidates: [{ question: "How long is deletion?", draftAnswer: "Seven days.", sourceLabel: "Slack", confidence: "high" }] };
    } else if (/decisionImpact/.test(query)) {
      data = { decisionImpact: { summary: "One runbook is affected.", docs: [{ title: "Retention runbook", source: "github", severity: "high", reason: "Policy changed" }] } };
    }
    await route.fulfill({ json: { data } });
  });
  await page.route("**/connectors/validate", async (route) => {
    const body = route.request().postDataJSON(); restCalls.push({ path: "/connectors/validate", body });
    await route.fulfill({ json: { ok: true, error: "" } });
  });
  await page.route("**/knowledge-chat-api/*/*", async (route) => {
    restCalls.push({ path: new URL(route.request().url()).pathname, body: null });
    await route.fulfill({ json: { name: "Company knowledge", title: "Ask Acme", welcome: "Ask about company policy.", project: "default" } });
  });
  await page.route("**/chat", async (route) => {
    const body = route.request().postDataJSON(); restCalls.push({ path: "/chat", body });
    await route.fulfill({ status: 200, contentType: "text/event-stream", body:
      'event: meta\ndata: {"session_id":41,"sources":[{"n":1,"source":"github","title":"Retention runbook","meta":"Canonical policy","document_id":1,"href":"/knowledge/doc?id=1"}]}\n\n' +
      'data: {"token":"Retention is 30 days [1]."}\n\nevent: done\ndata: {}\n\n' });
  });
  await page.route("**/connectors/connect", async (route) => {
    const body = route.request().postDataJSON(); restCalls.push({ path: "/connectors/connect", body });
    await route.fulfill({ json: { sourceId: 42 } });
  });
  await page.route("**/bots/slack/test", async (route) => {
    restCalls.push({ path: "/bots/slack/test", body: {} });
    await route.fulfill({ json: { ok: true, team: "Acme", user: "mari" } });
  });
  await page.route("**/bots/slack/setup", async (route) => {
    const body = route.request().postDataJSON();
    restCalls.push({ path: "/bots/slack/setup", body });
    await route.fulfill({ json: { ok: true, team: "Acme", teamId: "T-ACME",
      botUser: "mari", installationId: 5 } });
  });
  await page.route("**/onboard/upload", async (route) => {
    restCalls.push({ path: "/onboard/upload", body: "multipart" });
    await route.fulfill({ json: { ok: true, sourceId: 43, files: [{ name: "runbook.md", docId: 3, chunks: 1, embedded: 1 }] } });
  });
  for (const path of ["/auth/preferences/profile", "/auth/preferences/password", "/auth/preferences/notification"]) {
    await page.route(`**${path}`, async (route) => {
      const body = route.request().postDataJSON(); restCalls.push({ path, body });
      await route.fulfill({ json: { ok: true } });
    });
  }

  return {
    calls, restCalls,
    failNext: (pattern, message) => { failure = { pattern, message }; },
    failNextAuthCheck: (status = 503) => { authFailureStatus = status; },
    setData: (key, value) => { state[key] = value; },
  };
}
