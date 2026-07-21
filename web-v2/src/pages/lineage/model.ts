// Lineage page — shared model: relation styles, node/edge types, source
// identity, lens palettes, GraphQL query strings, and small pure helpers.
// No React in here.

import { SourceKey } from "../../data/sources";

// Five distinct hues, one per relation — no two share a color (contradicts
// and discussed used to both be #b23a1e, told apart only by dash pattern).
export const REL = {
  references: { color: "#1e6fa8", dash: undefined, label: "References" },
  discussed: { color: "#a05e1c", dash: undefined, label: "Discussed in" },
  derived: { color: "#2c6e49", dash: undefined, label: "Derived from" },
  translates: { color: "#6a5a9c", dash: "4 5", label: "Translates" },
  contradicts: { color: "#b23a1e", dash: "6 5", label: "Contradicts" },
} as const;

export type RelKey = keyof typeof REL;

// direction-aware labels for the grouped connections list
export const REL_OUT: Record<RelKey, string> = {
  references: "References", discussed: "Discussed in", derived: "Derived from",
  translates: "Translates", contradicts: "Contradicts",
};
export const REL_IN: Record<RelKey, string> = {
  references: "Referenced by", discussed: "Discusses", derived: "Source of",
  translates: "Translated by", contradicts: "Contradicted by",
};

// document classification (LINEAGE-ROLLUP-CONTRACT §Server half)
export type DocKind = "page" | "commit" | "pr" | "issue" | "answer" | "decision" | "seed";

export type LNode = {
  id: string;
  docId?: number;
  source: string;
  title: string;
  meta: string;
  icon: string;
  docKind: DocKind;
  group: string; // roll-up bucket, "" = ungrouped; e.g. "gh:MariHQ/web:commits"
  x: number; // 0..1 (server auto-layout / pinned position)
  y: number; // 0..1
  date?: string;        // ISO yyyy-mm-dd (last update) — powers the time axis
  createdDate?: string; // ISO — as-of filtering
  pinned?: boolean;
  warn?: boolean;
  owner?: string;
  tags?: string[];
  staleDays?: number;
  orphan?: boolean;
  inbound?: number;
  outbound?: number;
};

export type LEdge = {
  from: string;
  to: string;
  rel: RelKey;
  date?: string; // ISO
  llm?: boolean;
  dashed?: boolean;
  meta?: Record<string, any>;
};

export type Lens = "source" | "stale" | "owner" | "health";
export const LENSES: { key: Lens; label: string }[] = [
  { key: "source", label: "Source" },
  { key: "stale", label: "Staleness" },
  { key: "owner", label: "Ownership" },
  { key: "health", label: "Health" },
];

export type LayoutMode = "flow" | "timeline" | "circle" | "cluster";
// virtual space the timeline layout (and pin persistence) is normalized against
export const TL_W = 1600, TL_H = 900;

export type GraphStats = {
  docs: number; stale: number; orphans: number; untranslated: number;
  unowned: number; contradictions: number;
  topCited: { title: string; docId: number; inbound: number }[];
  activity: { date: string; count: number }[];
};
export type GraphView = { id: number; name: string; state: string };

// shareable view state — exactly what saved views persist and the URL carries
export type ViewState = {
  focus: string | null; lens: Lens; asof: string | null;
  rels: RelKey[]; scope: "focus" | "all"; chip: string | null;
  open: string[]; // expanded roll-up groups (?open=gh:...,gh:...)
};

export type Sel = { kind: "node" | "edge"; id: string };

type ImpactDoc = { title: string; source: string; severity: string; reason: string };
export type ImpactResult = { claim: string; summary: string; docs: ImpactDoc[] };
export type DocHistoryRow = { at: string; actor: string; verb: string; detail: string };

// ————— dates —————
export function isoToDate(iso: string) {
  return new Date(`${iso}T00:00:00`);
}

// ————— source identity (filter menu + node glyph/accent) —————

export const SOURCE_ORDER = ["github", "slack", "docs", "notion", "granola", "gdocs", "docsite"] as const;
export const SOURCE_LABELS: Record<string, string> = {
  github: "GitHub", slack: "Slack", docs: "Docs", notion: "Notion",
  granola: "Granola", gdocs: "Drive", docsite: "Doc site",
};
export const SOURCE_ICON_KEYS: SourceKey[] = ["github", "slack", "gdocs", "notion", "granola", "docs"];

// source identity on the canvas: a small text glyph + a subtle accent tint
export const SOURCE_GLYPH: Record<string, string> = {
  github: "⬡", slack: "✳", docs: "▤", notion: "▣", granola: "✎", gdocs: "▲", docsite: "◍",
};
export const SOURCE_ACCENT: Record<string, string> = {
  github: "#6f7c89", slack: "#6a5a9c", docs: "#35549d", notion: "#10263b",
  granola: "#2c6e49", gdocs: "#a05e1c", docsite: "#4a7d7b",
};

// unknown sources fall into the doc-site/globe group
export const srcKey = (source: string) => (SOURCE_LABELS[source] && source !== "docsite" ? source : "docsite");

// API edge kinds → relation styles on this page
export const KIND_TO_REL: Record<string, RelKey> = {
  content: "references", reference: "references", references: "references",
  links_to: "references", similar: "references", // extracted links (contract §Server half)
  impact: "discussed", discussed: "discussed",
  derived: "derived", translates: "translates", contradicts: "contradicts",
};

export const LINEAGE_QUERY = `{
  lineage { id docId source title meta icon x y pinned date createdDate warn owner tags staleDays orphan inbound outbound docKind group }
  lineageEdges { id fromId toId kind date curve meta }
}`;

// ————— roll-up groups (macro nodes for gh:<owner>/<repo>:<kind> buckets) —————

// cy-element id prefixes for the synthetic roll-up elements
export const MACRO_PREFIX = "grp:";
export const STUB_PREFIX = "more:";
export const AGG_EDGE_PREFIX = "ge:";
export const GROUP_PAGE_SIZE = 25;

const GROUP_KIND_WORDS: Record<string, [string, string]> = {
  commits: ["commit", "commits"], prs: ["PR", "PRs"], issues: ["issue", "issues"],
};

// "gh:MariHQ/web:commits" → { repo: "MariHQ/web", kind: "commits" }
export function groupParts(groupId: string) {
  const i = groupId.indexOf(":"), j = groupId.lastIndexOf(":");
  if (i < 0 || j <= i) return { repo: groupId, kind: "" };
  return { repo: groupId.slice(i + 1, j), kind: groupId.slice(j + 1) };
}
export function groupKindWord(kind: string, n: number) {
  const w = GROUP_KIND_WORDS[kind] ?? [kind, kind];
  return n === 1 ? w[0] : w[1];
}

export const STATS_QUERY = `{
  graphStats {
    docs stale orphans untranslated unowned contradictions
    topCited { title docId inbound }
    activity { date count }
  }
}`;

// ————— lens palettes —————

export const OWNER_PALETTE = ["#35549d", "#6a5a9c", "#2c6e49", "#1c3f60", "#a05e1c", "#4a7d7b", "#b23a1e", "#a04a6e"];
export function ownerColor(owner: string) {
  let h = 0;
  for (let i = 0; i < owner.length; i++) h = (h * 31 + owner.charCodeAt(i)) >>> 0;
  return OWNER_PALETTE[h % OWNER_PALETTE.length];
}
export const staleColor = (d: number) => (d <= 14 ? "#2c6e49" : d <= 45 ? "#a05e1c" : "#b23a1e");

export function downloadText(name: string, text: string, type: string) {
  const blob = new Blob([text], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

export const clamp = (n: number, min: number, max: number) => Math.min(max, Math.max(min, n));
export const edgeKey = (e: LEdge) => `${e.from}→${e.to}:${e.rel}`;

export const SEVERITY_TASK: Record<string, { kind: string; kindLabel: string }> = {
  "update-required": { kind: "stale", kindLabel: "Update" },
  review: { kind: "factcheck", kindLabel: "Review" },
  minor: { kind: "approval", kindLabel: "Minor" },
};

// hydrate view state from the URL (deep links round-trip through replaceState)
export function readUrl(): ViewState {
  const p = new URLSearchParams(window.location.search);
  const lensRaw = p.get("lens") as Lens | null;
  const rels = p.get("rels");
  return {
    focus: p.get("focus"),
    lens: lensRaw && LENSES.some((l) => l.key === lensRaw) ? lensRaw : "source",
    asof: p.get("asof"),
    rels: rels
      ? (rels.split(",").filter((k) => k in REL) as RelKey[])
      : (Object.keys(REL) as RelKey[]),
    scope: p.get("scope") === "all" ? "all" : "focus",
    chip: p.get("chip"),
    open: (p.get("open") ?? "").split(",").filter(Boolean),
  };
}
