/* Overview dashboard adapter.
 *
 * This is the reference for every other `src/data/<page>.ts`: one GraphQL
 * document, one mapper from the response onto the page's exported data type,
 * one hook returning `{ data, loading, error }` straight into the page props.
 *
 * All of the Overview widgets read from root query fields that already exist,
 * so a single document fetches the whole dashboard and the page gets one
 * honest loading state instead of eight racing ones. */

import type { ChipStatus } from "@mari-design/components";
import type { OverviewData } from "@mari-design/components/pages/OverviewPage";
import type { WfFlow, WfStep, WfRun } from "@mari-design/components/features/OverviewWorkflowStrip";
import type { PulseTileData } from "@mari-design/components/features/OverviewSourcePulse";
import type { RecentDoc } from "@mari-design/components/features/OverviewRecentDocs";
import type { ReviewTask } from "@mari-design/components/features/OverviewTodayReview";
import type { FeedItem } from "@mari-design/components/features/OverviewLiveActivity";
import type { DigestTopic } from "@mari-design/components/features/OverviewDigestCard";
import type { DateRange } from "@mari-design/components/data-display/DateRangePicker";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery } from "../lib/api";
import { useAuth } from "../lib/auth";
import { rangeFromParams, rangeVars } from "./range";
import type { PageData } from "./types";

/* ── query ──────────────────────────────────────────────────────────────── */

/* `since` bounds the "changes" count, which is the one stat that counts events
   over a window; the other two are gauges of the workspace right now (facts
   awaiting review, flows running) and stay as they are whatever window is
   picked, because that is what they measure. */
const QUERY = `query Overview($since: String) {
  overviewStats(since: $since)
  tasks { id title assigneeInitials kind kindLabel done }
  digest { title summary where { source label } impact { name tone } }
  activityFeed(limit: 12) { id kind actor text target secondsAgo }
  search(query: "", k: 6) { id source title date }
  sourcePulse { provider name status stat unit bars }
  workflows { id name status nodes }
  workflowRuns { id workflowId status started rows }
}`;

/** The live feed refresh cadence the page hands to the activity widget. */
const ACTIVITY_POLL_MS = 10_000;

/* ── response shape ─────────────────────────────────────────────────────── */

type Res = {
  overviewStats: { changes: number; factsReview: number; flowsRunning: number } | null;
  tasks: { id: number; title: string; assigneeInitials: string; kind: string; kindLabel: string; done: boolean }[];
  digest: {
    title: string; summary: string;
    where: { source: string; label: string }[];
    impact: { name: string; tone: string }[];
  }[];
  activityFeed: { id: number; kind: string; actor: string; text: string; target: string; secondsAgo: number }[];
  search: { id: number; source: string; title: string; date: string }[];
  sourcePulse: { provider: string; name: string; status: string; stat: string; unit: string; bars: number[] }[];
  workflows: { id: number; name: string; status: string; nodes: { kind: string; label: string }[] }[];
  workflowRuns: {
    id: number; workflowId: number; status: string; started: string;
    rows: { step: string; status: string }[] | null;
  }[];
};

/* ── mapping helpers ────────────────────────────────────────────────────── */

/* The workflow strip only draws steps it has an icon and a When/Do/Check/Then
   section for. A stored flow can legitimately hold a step kind this build of
   the library does not know yet (the flow editor and the library ship
   separately), and rendering an unknown kind blanks the node. Drop it instead. */
const STEP_KINDS = new Set([
  "trigger", "fetch_docs", "refine", "fact_check", "condition", "tag",
  "derive_links", "create_task", "approval", "deploy_site", "notify",
  "summarize", "sync_source", "refresh_digest",
]);

/* Flow-engine step results → the library's shared status vocabulary. Anything
   unrecognized becomes `undefined`, which renders the node with no chip at all
   rather than guessing at an outcome. */
const STEP_STATUS: Record<string, ChipStatus> = {
  passed: "succeeded",
  failed: "failed",
  running: "running",
  waiting: "queued",
  pending: "queued",
  skipped: "skipped",
};

/** Source chips carry the provider's mark; the API sends only its name. */

/** Pulse tiles have two tones. Anything the API does not call active reads as
 *  the quieter one — never as an invented third state. */
const pulseStatus = (s: string): PulseTileData["status"] =>
  s.toLowerCase() === "active" ? "active" : "moderate";

/** The dashboard strip shows one flow: the first active one, else the first. */
function pickFlow(res: Res): { flow: WfFlow; id: number } | null {
  const wfs = res.workflows ?? [];
  const wf = wfs.find((w) => w.status === "active") ?? wfs[0];
  if (!wf) return null;
  const run = (res.workflowRuns ?? [])
    .filter((r) => r.workflowId === wf.id)
    .sort((a, b) => b.id - a.id)[0];
  // Per-step outcomes come from the latest run's row list, matched on the step
  // label the engine recorded. No run yet = no states, and the strip then
  // suppresses its "last run checks" block entirely.
  const stateOf = new Map((run?.rows ?? []).map((r) => [r.step, STEP_STATUS[r.status?.toLowerCase()]]));
  const nodes: WfStep[] = (wf.nodes ?? [])
    .filter((n) => STEP_KINDS.has(n.kind))
    .map((n) => ({ kind: n.kind as WfStep["kind"], label: n.label, state: stateOf.get(n.label) }));
  return { flow: { name: wf.name, status: wf.status === "active" ? "active" : "paused", nodes }, id: wf.id };
}

function pickRun(res: Res, workflowId: number | null): WfRun {
  if (workflowId === null) return null;
  const run = (res.workflowRuns ?? [])
    .filter((r) => r.workflowId === workflowId)
    .sort((a, b) => b.id - a.id)[0];
  return run ? { started: run.started, outcome: run.status } : null;
}

/* ── mapper ─────────────────────────────────────────────────────────────── */

/** A workspace the API has not answered for yet. Not demo data: every
 *  collection is genuinely empty, and it is only ever rendered underneath
 *  `loading` or `error`. */
export const EMPTY: OverviewData = {
  personName: "",
  stats: { changes: 0, factsReview: 0, flowsRunning: 0 },
  tasks: [], digest: [], activity: [], docs: [], flow: null, run: null, sources: [],
  activityPollMs: ACTIVITY_POLL_MS,
};

export function mapOverview(
  res: Res, personName: string, timeZone?: string, range?: DateRange,
): OverviewData {
  const picked = pickFlow(res);
  return {
    personName,
    // The zone Preferences collects. Omitted when the account has not been
    // asked yet, and the greeting then reads the browser's own clock — which
    // is still the reader's real local time, never a fixed hour.
    timeZone: timeZone || undefined,
    range,
    stats: {
      changes: res.overviewStats?.changes ?? 0,
      factsReview: res.overviewStats?.factsReview ?? 0,
      flowsRunning: res.overviewStats?.flowsRunning ?? 0,
    },
    tasks: (res.tasks ?? []).map<ReviewTask>((t) => ({
      id: t.id, text: t.title, who: t.assigneeInitials,
      pill: t.kind, pillText: t.kindLabel, done: t.done,
    })),
    digest: (res.digest ?? []).map<DigestTopic>((d) => ({
      title: d.title,
      summary: d.summary,
      where: d.where ?? [],
      impact: d.impact ?? [],
    })),
    activity: (res.activityFeed ?? []).map<FeedItem>((a) => ({
      id: a.id, kind: a.kind, actor: a.actor, text: a.text,
      target: a.target, secondsAgo: a.secondsAgo,
    })),
    docs: (res.search ?? []).map<RecentDoc>((d) => ({
      id: d.id, source: d.source, title: d.title, date: d.date,
    })),
    flow: picked?.flow ?? null,
    run: pickRun(res, picked?.id ?? null),
    sources: (res.sourcePulse ?? []).map<PulseTileData>((s) => ({
      key: s.provider, name: s.name, stat: s.stat, unit: s.unit,
      status: pulseStatus(s.status), bars: s.bars ?? [],
    })),
    activityPollMs: ACTIVITY_POLL_MS,
  };
}

/* ── adapter ────────────────────────────────────────────────────────────── */

/* The signed-in account's own zone, from the same endpoint Preferences reads
   and writes — it is session state rather than workspace knowledge, so it does
   not travel with the dashboard query. "" until it answers, and the greeting
   then uses the browser's zone, which is the same clock the reader is looking
   at. No invented default: `/auth/me` does not carry a zone, so guessing one
   here would be guessing what time it is for someone. */
function useTimeZone(signedIn: boolean): string {
  const [zone, setZone] = useState("");
  useEffect(() => {
    if (!signedIn) return;
    let live = true;
    void (async () => {
      try {
        const res = await fetch("/auth/preferences");
        if (!res.ok) return;
        const body = (await res.json()) as { timezone?: string };
        if (live) setZone(String(body.timezone ?? ""));
      } catch {
        // A zone that cannot be read is not an error the dashboard reports:
        // the greeting falls back to the browser's clock and says nothing.
      }
    })();
    return () => { live = false; };
  }, [signedIn]);
  return zone;
}

export function useOverview(): PageData<OverviewData> {
  // The greeting name is session identity, not workspace content, so it comes
  // from the auth context rather than the dashboard query. Given name only:
  // "Good morning, Dana Rodriguez" reads like a summons.
  const { user } = useAuth();
  const personName = (user?.name ?? "").trim().split(/\s+/)[0] ?? "";
  const timeZone = useTimeZone(Boolean(user));

  const [params] = useSearchParams();
  // Unasked, "changes" counts the last seven days, which is what the tile has
  // always meant; the picker opens on that.
  const range = rangeFromParams(params) ?? { preset: "7d" as const };

  /* The response is cached raw and mapped on every render, not once per fetch:
     the name, the zone and the window all arrive from outside the query (auth,
     `/auth/preferences`, the route), and a mapper frozen at fetch time would
     keep showing whichever of them had not landed yet. */
  const q = useQuery<Res>(QUERY, {
    variables: { since: rangeVars(range).since },
    map: (d: Res) => d,
  });
  return {
    data: q.data ? mapOverview(q.data, personName, timeZone, range) : { ...EMPTY, range },
    loading: q.loading,
    error: q.error ? (q.errorText ?? "The dashboard is temporarily unavailable.") : null,
  };
}
