// Flows — step metadata, template copy, pure helpers.

import type { ComponentType, CSSProperties } from "react";
import * as Ic from "../../components/icons";
import { fmtDateTime } from "../../components/ui";

// ————— types —————

export type StepKind =
  | "trigger" | "fetch_docs" | "refine" | "fact_check" | "condition" | "tag"
  | "derive_links" | "create_task" | "approval" | "deploy_site" | "notify" | "summarize"
  | "sync_source" | "refresh_digest";

export type Step = { kind: StepKind; label: string; config: Record<string, any> };

/** Trigger — a document event, a schedule, or {} (all-null) for manual-only. */
export type Trigger = {
  on?: "document_added" | "document_changed" | "schedule" | "" | null;
  source_id?: number | null;
  tag?: string | null;
  path_glob?: string | null;
  /** schedule only — run cadence, 1..10080 (a week). */
  every_minutes?: number | null;
};

export type Workflow = {
  id: number; name: string; description: string; color: string;
  pinned: boolean; status: string; nodes: Step[]; trigger: Trigger | null;
};

type RunRow = { step: string; status: string; detail?: string; duration?: string };

export type Run = {
  id: number; workflowId: number; workflowName: string; number: number;
  status: string; started: string; duration: string; progress: number;
  stats: any; rows: RunRow[];
  /** Provenance for auto-started runs ("Triggered by: docs/x.md updated"); "" for manual. */
  triggeredBy: string;
};

export type EditorState = { id: number | null; name: string; description: string; steps: Step[] };

// ————— step metadata (When / Do / Check / Then) —————

export type Section = "when" | "do" | "check" | "then";

export const SECTION_OF: Record<StepKind, Section> = {
  trigger: "when",
  fetch_docs: "do", refine: "do", fact_check: "do", summarize: "do", tag: "do", derive_links: "do",
  sync_source: "do", refresh_digest: "do",
  condition: "check", approval: "check",
  create_task: "then", notify: "then", deploy_site: "then",
};

export const SECTION_META: Record<Section, { title: string; cls: string; color: string; tagline: string }> = {
  when: { title: "When", cls: "fl-chip--when", color: "var(--green-deep)", tagline: "something happens to knowledge" },
  do: { title: "Do", cls: "fl-chip--do", color: "var(--blue)", tagline: "editorial work on the matched docs" },
  check: { title: "Check", cls: "fl-chip--check", color: "#a05e1c", tagline: "gate the result" },
  then: { title: "Then", cls: "fl-chip--then", color: "var(--red)", tagline: "deliver where the team works" },
};

export const KIND_META: Record<StepKind, { name: string; desc: string; llm?: boolean; defLabel: string; defConfig: Record<string, any> }> = {
  trigger: {
    name: "Source event",
    desc: "Watches a scope of documents. Scheduled scan — the flow runs on its cadence or when you press Run, not the instant a doc changes.",
    defLabel: "When docs change", defConfig: { label: "", query: "" },
  },
  fetch_docs: {
    name: "Fetch docs",
    desc: "Pulls matching documents into the run — by search query, tag, or both, capped at k. Later steps run over this set.",
    defLabel: "Fetch docs", defConfig: { query: "", k: 3 },
  },
  refine: {
    name: "Prose refine", llm: true,
    desc: "Runs a Mari writing skill over each fetched doc and proposes edits as findings — never applies them silently.",
    defLabel: "Refine prose", defConfig: { skill: "tighten" },
  },
  fact_check: {
    name: "Fact check", llm: true,
    desc: "Checks claims in the fetched docs against accepted facts and reports contradictions with citations.",
    defLabel: "Verify facts", defConfig: {},
  },
  summarize: {
    name: "Summarize", llm: true,
    desc: "Drafts a summary of the fetched documents — useful for digests and translation drafts.",
    defLabel: "Summarize", defConfig: {},
  },
  tag: {
    name: "Tag docs",
    desc: "Applies a tag to every fetched document. Dry runs preview the tagging instead of writing it.",
    defLabel: "Tag docs", defConfig: { tag: "needs-review" },
  },
  derive_links: {
    name: "Derive links", llm: true,
    desc: "Suggests lineage links between the fetched docs and related knowledge.",
    defLabel: "Derive links", defConfig: {},
  },
  condition: {
    name: "Condition",
    desc: "Branches on a run stat (e.g. contradictions > 0). Steps marked “only on the yes-branch” run when it passes; otherwise they're skipped.",
    defLabel: "Contradictions?", defConfig: { field: "contradictions", greater_than: 0 },
  },
  approval: {
    name: "Approval",
    desc: "Pauses the run as “waiting” until the assignee approves it from the run panel. Nothing downstream runs until then.",
    defLabel: "Approval", defConfig: { assignee: "Aki K." },
  },
  create_task: {
    name: "Create task",
    desc: "Opens a task on the Tasks board. Dry runs preview the task instead of creating it.",
    defLabel: "Create task", defConfig: { title: "", kind: "review", kind_label: "Review" },
  },
  notify: {
    name: "Notify",
    desc: "Sends an in-app notification. Dry runs preview the message instead of sending it.",
    defLabel: "Notify", defConfig: { text: "", detail: "" },
  },
  deploy_site: {
    name: "Deploy site",
    desc: "Publishes a documentation site version. Dry runs report what would deploy without shipping anything.",
    defLabel: "Deploy site", defConfig: { site_id: 1 },
  },
  sync_source: {
    name: "Sync source",
    desc: "Runs the real diff-based sync for a connected source — fetch, chunk, embed — and reports exactly what changed. Pair with a schedule trigger for periodic syncs.",
    defLabel: "Sync source", defConfig: { source_id: 0 },
  },
  refresh_digest: {
    name: "Refresh digest", llm: true,
    desc: "Regenerates the weekly digest from recent documents and facts — the same engine behind the Overview digest card.",
    defLabel: "Refresh digest", defConfig: {},
  },
};

export const KIND_ICON: Record<StepKind, ComponentType<{ size?: number; style?: CSSProperties; strokeWidth?: number }>> = {
  trigger: Ic.Bell,
  fetch_docs: Ic.Doc,
  refine: Ic.Quill,
  fact_check: Ic.ShieldCheck,
  summarize: Ic.Layers,
  tag: Ic.Tag,
  derive_links: Ic.Fork,
  condition: Ic.Shuffle,
  approval: Ic.Eye,
  create_task: Ic.Clipboard,
  notify: Ic.Send,
  deploy_site: Ic.Globe,
  sync_source: Ic.Refresh,
  refresh_digest: Ic.Calendar,
};

// picker offers Do / Check / Then — a flow has exactly one trigger, always on top
export const PICKER_SECTIONS: { sec: Section; kinds: StepKind[] }[] = [
  { sec: "do", kinds: ["fetch_docs", "refine", "fact_check", "summarize", "tag", "derive_links", "sync_source", "refresh_digest"] },
  { sec: "check", kinds: ["condition", "approval"] },
  { sec: "then", kinds: ["create_task", "notify", "deploy_site"] },
];

export const REFINE_SKILLS = ["tighten", "clarify", "sharpen", "deslop", "understate", "polish"];
export const CONDITION_FIELDS = ["contradictions", "edits", "links", "facts", "docs"];
export const TASK_KINDS: [string, string][] = [
  ["review", "Review"], ["factcheck", "Fact check"], ["stale", "Stale"], ["approval", "Approval"],
];

// outcome-phrased template copy (mechanism lives in the small print)
export const TEMPLATE_OUTCOME: Record<string, string> = {
  "Docs guardrail": "Never merge a PR that contradicts your facts",
  "Slack digest": "Turn a week of discussion into a Monday digest",
  "Stale sweeper": "Docs that go quiet get flagged and assigned",
  "Translation sync": "Customer-facing edits ship with review-ready drafts",
};

// ————— small helpers —————

export const deepCopy = <T,>(x: T): T => JSON.parse(JSON.stringify(x));

export const isDry = (r: Run) => !!(r?.stats?.dry_run || r?.stats?.ctx?.dry_run);

export const fmtDur = (d?: string) => (!d || d === "00:00:00" ? "<1s" : d);

export const normStatus = (s: string) => {
  const x = (s || "").toLowerCase();
  if (x === "passed" || x === "completed" || x === "done") return "passed";
  if (x === "running") return "running";
  if (x === "waiting") return "waiting";
  if (x === "failed") return "failed";
  if (x === "skipped") return "skipped";
  return "pending";
};

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const STARTED_RE = /^([A-Z][a-z]{2})\s+(\d{1,2}),?\s+(\d{1,2}):(\d{2})\s*(AM|PM)$/;

/** The engine stores `started` as a "Mon DD, HH12:MI AM" label (zero-padded —
 * "Jul 16, 02:57 PM"). Re-render it through the canonical fmtDateTime. */
export function fmtStarted(label: string): string {
  const m = STARTED_RE.exec((label ?? "").trim());
  if (!m) return label;
  const month = MONTHS.indexOf(m[1]);
  if (month < 0) return label;
  let h = Number(m[3]) % 12;
  if (m[5] === "PM") h += 12;
  return fmtDateTime(new Date(new Date().getFullYear(), month, Number(m[2]), h, Number(m[4])));
}

export function stepSummary(s: Step, sites: { id: number; name: string }[]): string {
  const c = s.config ?? {};
  switch (s.kind) {
    case "trigger": return c.label || "manual — runs when you press Run";
    case "fetch_docs": {
      const bits = [];
      if (c.query) bits.push(`matching “${c.query}”`);
      if (c.tag) bits.push(`tagged '${c.tag}'`);
      bits.push(`top ${c.k ?? 3}`);
      return bits.join(" · ");
    }
    case "refine": return `Mari skill: ${c.skill ?? "tighten"} — proposes edits`;
    case "fact_check": return "against accepted facts · reports contradictions";
    case "summarize": return "drafts a summary of the fetched docs";
    case "tag": return `apply '${c.tag ?? "needs-review"}' to fetched docs`;
    case "derive_links": return "suggest lineage links between related docs";
    case "condition": return `branch when ${c.field ?? "contradictions"} > ${c.greater_than ?? 0}`;
    case "create_task": return `“${c.title || "Untitled task"}”${c.assignee ? ` → ${c.assignee}` : ""}${c.only_if_branch ? " · only on the yes-branch" : ""}`;
    case "approval": return `pauses the run until ${c.assignee ?? "someone"} approves`;
    case "notify": return c.text ? `“${c.text}”${c.user ? ` → ${c.user}` : ""}` : "sends a notification";
    case "deploy_site": {
      const site = sites.find((x) => Number(x.id) === Number(c.site_id));
      return `publish ${site ? site.name : `site #${c.site_id ?? 1}`}`;
    }
    case "sync_source": return `diff-sync connected source #${c.source_id ?? "?"} — fetch · chunk · embed`;
    case "refresh_digest": return "regenerate the weekly digest topics";
  }
}

export function runHeadline(r: Run): string {
  const rows = r.rows ?? [];
  for (let i = rows.length - 1; i > 0; i--) {
    const d = rows[i]?.detail;
    if (d && d.trim()) return d;
  }
  return rows[0]?.detail?.trim() || "—";
}

export function triggerSummary(w: Workflow): string {
  const t = w.nodes?.[0];
  return t?.config?.label || t?.label || "Manual";
}

// ————— document triggers —————

export type SourceOpt = { id: number; provider: string; name: string };

export const hasTrigger = (t: Trigger | null | undefined): boolean => !!t?.on;

/** "Every 10 min" / "Every hour" / "Every day" / "Every week" — cadence in minutes. */
export function fmtEvery(n: number): string {
  if (n >= 10080 && n % 10080 === 0) return n === 10080 ? "Every week" : `Every ${n / 10080} weeks`;
  if (n >= 1440 && n % 1440 === 0) return n === 1440 ? "Every day" : `Every ${n / 1440} days`;
  if (n >= 60 && n % 60 === 0) return n === 60 ? "Every hour" : `Every ${n / 60} hours`;
  return `Every ${n} min`;
}

/** Human summary of a trigger — "Every 10 min", or
 * "On doc change · source: MariHQ/mari-cli · path: docs/**". */
export function describeTrigger(t: Trigger | null | undefined, sources: SourceOpt[]): string {
  if (!hasTrigger(t)) return "Manual only";
  if (t!.on === "schedule") return fmtEvery(Number(t!.every_minutes) || 0);
  const bits = [t!.on === "document_added" ? "On doc added" : "On doc change"];
  if (t!.source_id != null) {
    const s = sources.find((x) => Number(x.id) === Number(t!.source_id));
    bits.push(`source: ${s ? s.name : `#${t!.source_id}`}`);
  }
  if (t!.tag) bits.push(`tag: ${t!.tag}`);
  if (t!.path_glob) bits.push(`path: ${t!.path_glob}`);
  return bits.join(" · ");
}
