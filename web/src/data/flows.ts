/* Flows adapter — the flow list, the sources a document trigger can name, and
 * the run history under it.
 *
 * `/flows` is the list surface and `/flows?flow=<id>` is that flow's pipeline
 * editor — the surface a newly created flow lands on, where its steps get
 * built. `/flows?new` opens the create drawer, so "New flow" is a place the
 * app can link to. The run inspector and the run-history surfaces have no
 * route yet, so they are honestly absent rather than opened onto a guess. */

import { useEffect, useMemo, useRef } from "react";
import { useSearchParams } from "react-router-dom";

import type { FlowsData, FlowsEditor } from "@mari-design/components/pages/FlowsPage";
import type { Flow, SourceRef } from "@mari-design/components/features/FlowsList";
import {
  STEP_KINDS, type EditorStep, type SiteRef, type StepKind,
} from "@mari-design/components/features/FlowsPipelineEditor";
import type { RunStat, RunStepRow, RunStatus, WorkflowRun } from "@mari-design/components/workflow/RunHistory";
import { useQuery } from "../lib/api";
import type { PageData } from "./types";

/* ── query ──────────────────────────────────────────────────────────────── */

/* The editor's pickers are real workspace data, not a vocabulary this file
   invents: an assignee it offers must be someone who can be assigned, a deploy
   target must be a site that exists, a tag must be a tag. */
const QUERY = `{
  workflows { id name description color status nodes trigger }
  workflowRuns { id workflowId workflowName number status started duration stats rows triggeredBy }
  sourcePulse { id name }
  members { name }
  sites { id name }
  tagDefs { tag }
}`;

type Res = {
  workflows: {
    id: number; name: string; description: string; color: string; status: string;
    // The engine's own step shape: `kind` picks the implementation, `config`
    // is that step's settings, `only_if_branch` puts it on a condition's
    // yes-branch. The list only needs the label; the editor needs all of it.
    nodes: {
      kind?: string; label?: string;
      config?: Record<string, unknown> | null;
      only_if_branch?: boolean | null;
    }[] | null;
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
  members: { name: string }[];
  sites: { id: number; name: string }[];
  tagDefs: { tag: string }[];
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

/* ── the pipeline editor ────────────────────────────────────────────────── */

/** A stored step, narrowed to the kinds this build of the editor can draw and
 *  configure. A kind it has no panel for would render as a step with no
 *  settings behind it, so it is dropped — the server and the library ship
 *  separately. */
const KNOWN_KIND = new Set<string>(STEP_KINDS);

export function mapSteps(nodes: Res["workflows"][number]["nodes"]): EditorStep[] {
  return (nodes ?? [])
    .map((n): EditorStep | null => {
      const kind = n.kind ?? "";
      if (!KNOWN_KIND.has(kind)) return null;
      return {
        kind: kind as StepKind,
        label: n.label ?? kind,
        config: (n.config ?? {}) as Record<string, unknown>,
        only_if_branch: n.only_if_branch ?? undefined,
      };
    })
    .filter((s): s is EditorStep => s !== null);
}

/** The editor, open on one flow. Null when no `?flow=` names a real workflow —
 *  a page cannot edit a row that is not there. */
export function buildEditor(res: Res, flowId: number | null, runs: WorkflowRun[]): FlowsEditor | null {
  if (flowId == null) return null;
  const w = (res.workflows ?? []).find((x) => x.id === flowId);
  if (!w) return null;
  const mine = new Set((res.workflowRuns ?? []).filter((r) => r.workflowId === w.id).map((r) => String(r.id)));
  return {
    id: w.id,
    name: w.name,
    description: w.description,
    enabled: w.status === "active",
    steps: mapSteps(w.nodes),
    runs: runs.filter((r) => mine.has(r.id)),
    members: (res.members ?? []).map((m) => m.name).filter(Boolean),
    sites: (res.sites ?? []).map<SiteRef>((s) => ({ id: s.id, name: s.name })),
    tags: (res.tagDefs ?? []).map((t) => t.tag).filter(Boolean),
  };
}

/* ── mapper ─────────────────────────────────────────────────────────────── */

/** A workspace with no automation at all, on any surface — which is exactly
 *  what makes the page's own `isEmpty` fire. */
export const EMPTY: FlowsData = {
  flows: [], sources: [], creating: false, editor: null, runPanel: null, runHistory: null,
  trigger: null, extras: null,
};

/** Pure: the whole response → everything the surface named by the route
 *  renders. `flowId` is `?flow=`, `creating` is `?new`. */
export function buildFlows(res: Res | null, flowId: number | null = null, creating = false): FlowsData {
  if (!res) return { ...EMPTY, creating };
  const runs = mapRuns(res);
  return {
    flows: mapFlows(res, runs),
    sources: (res.sourcePulse ?? []).map<SourceRef>((s) => ({ id: s.id, name: s.name })),
    creating,
    editor: buildEditor(res, flowId, runs),
    // `runHistory` would replace the flow list with the history table (see
    // FlowsPage's Body), so it stays null until this app routes to it.
    runPanel: null,
    runHistory: null,
    trigger: null,
    extras: null,
  };
}

/* ── adapter ────────────────────────────────────────────────────────────── */

export function useFlows(): PageData<FlowsData> {
  const [params] = useSearchParams();
  const flow = Number(params.get("flow"));
  const flowId = Number.isInteger(flow) && flow > 0 ? flow : null;
  const creating = params.get("new") !== null;
  const q = useQuery<Res>(QUERY, { map: (d: Res) => d });

/* The route names the subject; the query behind it takes no variables, so
   navigating from the list to one flow does not change useQuery's key and
   the page would render the new flow off a response taken before it
   existed. Creating one lands on exactly that route, so the read is re-run
   whenever the subject changes. Mount is not a change: the ref starts on the
   subject the first render already fetched. */
  const { refetch } = q;
  const seen = useRef<number | null>(flowId);
  useEffect(() => {
    if (seen.current === flowId) return;
    seen.current = flowId;
    refetch();
  }, [flowId, refetch]);

  /* Built once per RESPONSE, not once per render. The pipeline editor and the
     run panel resync their local draft/selection when the props they were
     seeded from change identity (the seen-sentinel idiom), and a mapper that
     rebuilt `steps` and `runs` on every render would hand them a new array on
     every keystroke — resetting the draft the user is typing into. */
  const data = useMemo(
    () => (q.data ? buildFlows(q.data, flowId, creating) : { ...EMPTY, creating }),
    [q.data, flowId, creating],
  );

  return {
    data,
    loading: q.loading,
    error: q.error ? (q.errorText ?? "Flows are temporarily unavailable.") : null,
  };
}
