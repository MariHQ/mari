/* Workflows adapter — both tabs of /workflows in one read.
 *
 * The page is one page, so it is one query: the observed runs (filtered and
 * paged by the server, so the rows and the total always describe the same
 * set), the workflow ?trajectory= names, and the approved answers those runs
 * are promoted into. Splitting it in two would mean the Observed tab could not
 * tell you which of its runs had already produced an answer without a second
 * round trip per card.
 *
 * This replaces src/data/trajectories.ts and src/data/answers.ts.
 */

import { useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import type {
  ObservedData, TrajectoryRow, WorkflowsData, WorkflowsTab,
} from "@mari-design/components/pages/WorkflowsPage";
import type {
  AnswerStat, AnswersData, HarvestSource,
} from "@mari-design/components/features/ApprovedAnswers";
import type { Answer } from "@mari-design/components/features/AnswerCard";
import { useQuery } from "../lib/api";
import type { PageData } from "./types";

const PAGE = 25;

/* One row shape, spelled once, used by both the list and the focused read. A
   deep-linked workflow and the same workflow in the list must be the same
   object or the drawer would render a thinner version of the card. */
const ROW = `
  id sessionId prompt status model layer1 layer2 category macroIntent phases
  stepCount failureCount reworkCount startedAt completedAt disposition
  steps { ordinal tool actionFamily args summary ok disposition editedArgs }
  evidence { documentId title reason rank relevance note }
  promotedWorkflowId
  promotedWorkflow { id name status nodeCount }
`;

const QUERY = `query Workflows(
  $limit: Int!, $offset: Int!, $category: String, $status: String,
  $failures: String, $search: String, $focus: Int
) {
  trajectories(limit: $limit, offset: $offset, category: $category,
               status: $status, failures: $failures, search: $search) { ${ROW} }
  trajectory(id: $focus) { ${ROW} }
  trajectoryTotal(category: $category, status: $status, failures: $failures, search: $search)
  trajectoryCategories
  trajectoryStatuses
  approvedAnswers {
    id question answer status owner channels sources served spark updated
    trajectoryId supersedes recheckAfter
  }
  answerCoverageGaps(limit: 8)
  answerHarvestSources
}`;

type AnswerRes = {
  id: number; question: string; answer: string; status: string; owner: string;
  channels: string[]; sources: { source: string; title: string }[] | null;
  served: number; spark: number[]; updated: string;
  trajectoryId: number | null; supersedes: number | null; recheckAfter: string;
};

type Res = {
  trajectories: TrajectoryRow[];
  trajectory: TrajectoryRow | null;
  trajectoryTotal: number;
  trajectoryCategories: string[];
  trajectoryStatuses: string[];
  approvedAnswers: AnswerRes[];
  answerCoverageGaps: string[];
  answerHarvestSources: { key: string; label: string; count: number }[] | null;
};

/* ── observed ───────────────────────────────────────────────────────────── */

/** Fill in what an older server may not send yet, so a row is always the shape
    the page destructures. Never invents a value the response could carry. */
function row(raw: TrajectoryRow): TrajectoryRow {
  return {
    ...raw,
    steps: (raw.steps ?? []).map((step) => ({
      ...step,
      disposition: step.disposition ?? "included",
      editedArgs: step.editedArgs ?? null,
    })),
    evidence: raw.evidence ?? [],
    phases: raw.phases ?? [],
    promotedWorkflowId: raw.promotedWorkflowId ?? null,
    promotedWorkflow: raw.promotedWorkflow ?? null,
    disposition: raw.disposition || "observed",
  };
}

/** What the URL says the Observed tab is looking at. */
export type ObservedParams = {
  category: string | null;
  status: string | null;
  failures: string | null;
  search: string;
  offset: number;
  focus: number | null;
};

export function buildObserved(res: Res | null, params: ObservedParams): ObservedData {
  return {
    rows: (res?.trajectories ?? []).slice(0, PAGE).map(row),
    total: res?.trajectoryTotal ?? 0,
    categories: res?.trajectoryCategories ?? [],
    statuses: res?.trajectoryStatuses ?? [],
    category: params.category,
    status: params.status,
    failures: params.failures,
    search: params.search,
    offset: params.offset,
    limit: PAGE,
    // Read directly rather than looked up in `rows`: the deep-linked workflow
    // is very often not on the page the filters currently show, and the drawer
    // has to open on it anyway.
    focused: res?.trajectory ? row(res.trajectory) : null,
  };
}

/* ── answers ────────────────────────────────────────────────────────────── */

const STATUS = new Set<Answer["status"]>(["approved", "draft", "retired"]);

/* The card draws a serving toggle per channel and has a label for exactly
   three. A channel a newer bot registered has no toggle, so it is dropped
   rather than rendered as an unnamed switch. */
const CHANNELS = new Set<Answer["channels"][number]>(["slack-bot", "support-widget", "docs-site"]);

export function mapAnswers(res: { approvedAnswers: AnswerRes[] }): Answer[] {
  return (res.approvedAnswers ?? [])
    // status is the card's whole frame — badge, actions and tone. A row whose
    // status this build does not know would render as an untitled card.
    .filter((a) => STATUS.has(a.status as Answer["status"]))
    .map<Answer>((a) => ({
      id: a.id,
      question: a.question,
      answer: a.answer,
      status: a.status as Answer["status"],
      owner: a.owner,
      channels: (a.channels ?? []).filter((c): c is Answer["channels"][number] =>
        CHANNELS.has(c as Answer["channels"][number])),
      sources: a.sources ?? [],
      served: a.served,
      // [] is a real answer: an answer nobody has been served has no curve, and
      // the card then draws no sparkline rather than a flat invented one.
      spark: a.spark ?? [],
      updated: a.updated,
      trajectoryId: a.trajectoryId ?? null,
      // "" means the row carries no recheck date. Passing it through as ""
      // would draw a chip reading "Recheck Invalid Date".
      recheckAfter: a.recheckAfter || undefined,
    }));
}

/* The headline strip. The counts are the answers we already have; the labels,
   tones and captions are the page's vocabulary for them, not the API's. */
const TILES: { label: string; sub: string; tone: AnswerStat["tone"]; of: (a: Answer[]) => number }[] = [
  { label: "Approved", sub: "serving verbatim", tone: "ok", of: (a) => a.filter((x) => x.status === "approved").length },
  { label: "Drafts", sub: "awaiting review", tone: "attention", of: (a) => a.filter((x) => x.status === "draft").length },
  { label: "Served", sub: "all time", tone: "info", of: (a) => a.reduce((n, x) => n + (x.served ?? 0), 0) },
];

/* What the scan can actually read, in the order the wizard offers it. The keys
   are the three `scanAnswerCandidates` accepts; the labels and descriptions are
   the console's words for them, and the COUNT decides whether a source is
   offered at all — a workspace with no Slack was previously invited to harvest
   Slack and got nothing back. Every offered source starts selected: there is
   no reason to open the wizard with a source you have switched off. */
export function mapHarvestSources(res: Pick<Res, "answerHarvestSources">): HarvestSource[] {
  return (res.answerHarvestSources ?? [])
    .filter((source) => source.count > 0)
    .map((source) => ({
      key: source.key,
      label: source.label,
      desc: source.key === "chat"
        ? `${source.count.toLocaleString("en-US")} recent questions asked in chat.`
        : `${source.count.toLocaleString("en-US")} indexed documents available to mine.`,
      on: true,
    }));
}

/** Pure: the answers + coverage gaps → everything the answers tab renders.
    `filter` is only which status tab opens selected: the tab's own strip
    filters the list, so handing it a pre-filtered one would leave the other
    tabs with nothing to show. */
export function buildAnswers(
  answers: Answer[], coverage: string[], filter: AnswersData["filter"],
  harvestSources: HarvestSource[] = [],
): AnswersData {
  return {
    // Empty means there is nothing to scan, and no "Harvest questions" button
    // is drawn — which is the truth about a workspace with no corpus yet.
    harvestSources,
    stats: TILES.map<AnswerStat>((t) => ({
      value: t.of(answers).toLocaleString("en-US"), label: t.label, tone: t.tone, sub: t.sub,
    })),
    filter,
    answers,
    coverage,
    pane: { kind: "answers" },
  };
}

/* ── the page ───────────────────────────────────────────────────────────── */

export function buildWorkflows(
  res: Res | null, tab: WorkflowsTab, params: ObservedParams,
): WorkflowsData {
  return {
    tab,
    observed: buildObserved(res, params),
    answers: buildAnswers(
      res ? mapAnswers(res) : [],
      res?.answerCoverageGaps ?? [],
      "all",
      res ? mapHarvestSources(res) : [],
    ),
  };
}

export const EMPTY: WorkflowsData = buildWorkflows(null, "observed", {
  category: null, status: null, failures: null, search: "", offset: 0, focus: null,
});

/** Only these two narrow anything. A `?tab=` this build does not know opens
    the tab the page opens by default rather than a blank one. */
const TABS = new Set<WorkflowsTab>(["observed", "answers"]);

/** "with" or "none". Anything else is ignored, so a stale link cannot filter
    the list down to a rule the toolbar has no way to show or undo. */
const FAILURES = new Set(["with", "none"]);

export function useWorkflows(): PageData<WorkflowsData> {
  const [params] = useSearchParams();
  const rawTab = params.get("tab") as WorkflowsTab | null;
  const tab: WorkflowsTab = rawTab && TABS.has(rawTab) ? rawTab : "observed";

  const rawOffset = Number(params.get("offset") || 0);
  const rawFocus = Number(params.get("trajectory") || 0);
  const observedParams: ObservedParams = {
    category: params.get("category") || null,
    status: params.get("status") || null,
    failures: FAILURES.has(params.get("failures") || "") ? params.get("failures") : null,
    search: params.get("q") || "",
    offset: Number.isInteger(rawOffset) && rawOffset >= 0 ? rawOffset : 0,
    focus: Number.isInteger(rawFocus) && rawFocus > 0 ? rawFocus : null,
  };

  const query = useQuery<Res>(QUERY, {
    variables: {
      limit: PAGE,
      offset: observedParams.offset,
      category: observedParams.category,
      status: observedParams.status,
      failures: observedParams.failures,
      search: observedParams.search || null,
      focus: observedParams.focus,
    },
    map: (data: Res) => data,
  });

  const data = useMemo(
    () => buildWorkflows(query.data, tab, observedParams),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the params object
    // is rebuilt every render; its fields are the real dependencies.
    [query.data, tab, observedParams.category, observedParams.status,
      observedParams.failures, observedParams.search, observedParams.offset,
      observedParams.focus],
  );

  return {
    data,
    loading: query.loading,
    error: query.error ? (query.errorText ?? "Workflows are temporarily unavailable.") : null,
  };
}
