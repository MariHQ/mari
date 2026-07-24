/* Flows adapter — the flow list, the sources a document trigger can name, and
 * the run history under it.
 *
 * `/flows` is the list surface. The pipeline editor, the run inspector and the
 * trigger drawer are surfaces the page derives from `editor` / `runPanel` /
 * `trigger` being present; this app has no routes for them yet, so they are
 * honestly absent rather than opened onto a guessed flow. */

import type { FlowsData } from "@mari-design/components/pages/FlowsPage";
import type { Flow, SourceRef } from "@mari-design/components/features/FlowsList";
import type { RunStat, RunStepRow, RunStatus, WorkflowRun } from "@mari-design/components/workflow/RunHistory";
import { useQuery } from "../lib/api";
import type { PageData } from "./types";

/* ── query ──────────────────────────────────────────────────────────────── */

const QUERY = `{
  workflows { id name description color status nodes trigger }
  workflowRuns { id workflowId workflowName number status started duration stats rows triggeredBy }
  sourcePulse { id name }
}`;

type Res = {
  workflows: {
    id: number; name: string; description: string; color: string; status: string;
    nodes: { kind?: string; label?: string }[] | null;
    trigger: { on?: string; source_id?: number | null; tag?: string | null; path_glob?: string | null; every_minutes?: number | null } | null;
  }[];
  workflowRuns: {
    id: number; workflowId: number; workflowName: string; number: number; status: string;
    started: string; duration: string;
    stats: Record<string, unknown> | null;
    rows: { step?: string; status?: string; detail?: string; duration?: string }[] | null;
    triggeredBy: string;
  }[];
  sourcePulse: { id: number; name: string }[];
};

/* ── mapping helpers ────────────────────────────────────────────────────── */

/* The one run vocabulary the chips, the spine and the history table all share.
   A status outside it would render an uncolored dot with no label. */
const RUN_STATUS = new Set<RunStatus>(["passed", "running", "waiting", "failed", "skipped", "pending"]);
const runStatus = (s: string): RunStatus | null =>
  RUN_STATUS.has(s as RunStatus) ? (s as RunStatus) : null;

/** Schedule intervals the trigger editor has presets for; anything else reads
 *  in minutes rather than being rounded into a preset it is not. */
const EVERY: Record<number, string> = { 10: "Every 10 minutes", 60: "Every hour", 1440: "Every day", 10080: "Every week" };

/** The "When …" line on a flow card, read off the stored trigger. */
export function whenLabel(trigger: Res["workflows"][number]["trigger"]): string {
  const on = trigger?.on ?? "";
  if (on === "schedule") {
    const every = trigger?.every_minutes ?? 0;
    return EVERY[every] ?? (every > 0 ? `Every ${every} minutes` : "On a schedule");
  }
  if (on === "document_added") return "Document added";
  if (on === "document_changed") return "Document changed";
  return "Manual only";
}

/** The stored trigger, narrowed to the three events the trigger editor offers.
 *  A `trigger.on` this build has no form for reads as manual-only, which is
 *  what an unconfigurable trigger effectively is. */
function mapTrigger(t: Res["workflows"][number]["trigger"]): Flow["trigger"] {
  if (!t) return null;
  const on = t.on ?? "";
  return {
    on: on === "schedule" || on === "document_added" || on === "document_changed" ? on : "",
    source_id: t.source_id ?? null,
    tag: t.tag ?? null,
    path_glob: t.path_glob ?? null,
    every_minutes: t.every_minutes ?? null,
  };
}

/* flowengine persists `stats` as {ctx: …, contradictions, edits, links} — the
   ctx is the run's working memory, not a figure, and `paused_at` / `note` are
   bookkeeping. Only the real per-run counters become tiles. */
const STAT_LABEL: Record<string, string> = {
  contradictions: "Contradictions", edits: "Edits", links: "Links",
};

function mapStats(stats: Record<string, unknown> | null): RunStat[] | undefined {
  if (!stats) return undefined;
  const out = Object.entries(STAT_LABEL)
    .filter(([key]) => typeof stats[key] === "number")
    .map<RunStat>(([key, label]) => ({
      label, value: stats[key] as number, bad: key === "contradictions" && (stats[key] as number) > 0,
    }));
  return out.length ? out : undefined;
}

function mapRows(rows: Res["workflowRuns"][number]["rows"]): RunStepRow[] | undefined {
  if (!rows?.length) return undefined;
  const out = rows
    .map((r): RunStepRow | null => {
      const status = runStatus(r.status ?? "");
      if (!r.step || !status) return null;
      return { step: r.step, status, detail: r.detail || undefined, duration: r.duration || undefined };
    })
    .filter((r): r is RunStepRow => r !== null);
  return out.length ? out : undefined;
}

export function mapRuns(res: Res): WorkflowRun[] {
  return (res.workflowRuns ?? [])
    .filter((r) => runStatus(r.status))
    .map<WorkflowRun>((r) => ({
      id: String(r.id),
      number: r.number,
      workflowName: r.workflowName,
      status: runStatus(r.status)!,
      started: r.started,
      duration: r.duration || undefined,
      triggeredBy: r.triggeredBy || undefined,
      rows: mapRows(r.rows),
      stats: mapStats(r.stats),
      // `dry` and `headline` are not columns: the engine records neither a
      // dry-run flag nor a one-line summary, so the table draws neither
      // rather than labelling every run as a real one with an invented
      // summary. (See the report.)
    }));
}

export function mapFlows(res: Res, runs: WorkflowRun[]): Flow[] {
  return (res.workflows ?? []).map<Flow>((w) => {
    // Newest first: workflowRuns comes back ordered by run number descending.
    const mine = (res.workflowRuns ?? []).filter((r) => r.workflowId === w.id);
    const last = mine.map((r) => ({ r, status: runStatus(r.status) })).find((x) => x.status);
    return {
      id: w.id,
      name: w.name,
      description: w.description,
      color: w.color,
      // The card has two states. A workflow the engine has paused, archived or
      // anything else is not running, which is what "paused" says.
      status: w.status === "active" ? "active" : "paused",
      whenLabel: whenLabel(w.trigger),
      trigger: mapTrigger(w.trigger),
      nodes: (w.nodes ?? [])
        .map((n) => ({ label: n.label ?? n.kind ?? "" }))
        .filter((n) => n.label !== ""),
      lastRun: last ? { status: last.status!, started: last.r.started } : null,
      recentRuns: runs
        .filter((r) => mine.some((m) => String(m.id) === r.id))
        .map((r) => ({ number: r.number, status: r.status }))
        .reverse(),
    };
  });
}

/* ── mapper ─────────────────────────────────────────────────────────────── */

/** A workspace with no automation at all, on any surface — which is exactly
 *  what makes the page's own `isEmpty` fire. */
export const EMPTY: FlowsData = {
  flows: [], sources: [], editor: null, runPanel: null, runHistory: null,
  trigger: null, extras: null,
};

/** Pure: the whole response → everything the list surface renders. */
export function buildFlows(res: Res | null): FlowsData {
  if (!res) return EMPTY;
  const runs = mapRuns(res);
  return {
    flows: mapFlows(res, runs),
    sources: (res.sourcePulse ?? []).map<SourceRef>((s) => ({ id: s.id, name: s.name })),
    // The list surface only. `runHistory` would replace the flow list with the
    // history table (see FlowsPage's Body), so it stays null until this app
    // routes to it.
    editor: null,
    runPanel: null,
    runHistory: null,
    trigger: null,
    extras: null,
  };
}

/* ── adapter ────────────────────────────────────────────────────────────── */

export function useFlows(): PageData<FlowsData> {
  const q = useQuery<FlowsData>(QUERY, { map: buildFlows });
  return {
    data: q.data ?? EMPTY,
    loading: q.loading,
    error: q.error ? (q.errorText ?? "Flows are temporarily unavailable.") : null,
  };
}
