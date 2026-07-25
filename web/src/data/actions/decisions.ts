/* Decision-ledger writes.
 *
 * Not wired, deliberately: the card's "Ignore" control. `supersedeDecision`
 * needs the statement that replaces the one being set aside — and it INSERTS
 * that statement as a new ratified decision. The button collects no such text,
 * so wiring it would file a decision literally named "(pending replacement)".
 * It keeps its local behaviour until the control asks for a replacement.
 */

import type { DecisionsActions } from "@mari-design/components/pages/DecisionsPage";
import type { ScanRun } from "@mari-design/components/features/ScanRunCard";
import type { RunStatus } from "@mari-design/components/workflow/RunHistory";
import { gqlResult } from "../../lib/api";
import { mutate } from "../actions";
import { decisionsChanged } from "../decisions";

const CAPTURE = `mutation AddDecision($statement: String!, $context: String!, $sourceLabel: String!) {
  addDecision(statement: $statement, context: $context, sourceLabel: $sourceLabel)
}`;

const RATIFY = `mutation RatifyDecision($id: Int!) { ratifyDecision(id: $id) }`;

const IMPACT = `mutation DecisionImpact($id: Int!) {
  decisionImpact(id: $id) { summary docs { title source severity reason } }
}`;


type ImpactRes = {
  decisionImpact: { summary: string; docs: { title: string; source: string; severity: string; reason: string }[] };
};

const START_SCAN = `mutation { startDecisionScan }`;

const RUN_QUERY = `query($id: Int!) {
  workflowRun(id: $id) { id number workflowName status progress stats rows }
}`;

type RunRes = {
  workflowRun: {
    id: number; number: number; workflowName: string; status: string; progress: number;
    stats: { decisions?: number } | null;
    rows: { step?: string; status?: string; detail?: string; duration?: string }[] | null;
  } | null;
};

/* The engine's step vocabulary is the library's, one word for one word. An
   unknown word reads as still running rather than as a state never reported. */
const RUN_STATUS = new Set<RunStatus>(["passed", "running", "waiting", "failed", "skipped", "pending"]);
const asStatus = (s: string): RunStatus => (RUN_STATUS.has(s as RunStatus) ? (s as RunStatus) : "running");

async function readRun(id: string): Promise<ScanRun> {
  const r = await gqlResult<RunRes>(RUN_QUERY, { id: Number(id) });
  if (!r.ok) throw new Error(r.error);
  const run = r.data?.workflowRun;
  if (!run) throw new Error("That scan is no longer on record.");
  const status = asStatus(run.status);
  // A stopped run has landed whatever it was going to land, so the ledger
  // under the page re-reads instead of showing the rows from before it.
  if (status !== "running" && status !== "pending") decisionsChanged();
  return {
    id,
    label: `${run.workflowName} · run #${run.number}`,
    status,
    progress: run.progress ?? 0,
    steps: (run.rows ?? []).map((x) => ({
      step: x.step ?? "step", status: asStatus(x.status ?? ""), detail: x.detail ?? "", duration: x.duration,
    })),
    added: typeof run.stats?.decisions === "number" ? run.stats.decisions : null,
  };
}

export function decisionsActions(): DecisionsActions {
  return {
    capture: async ({ statement, context, source }) => {
      // "" is a real value here: the resolver files an uncredited capture as
      // "Captured in Mari" rather than inventing a channel it never read.
      await mutate(CAPTURE, { statement, context, sourceLabel: source });
    },
    ratify: async ({ id }) => { await mutate(RATIFY, { id }); },
    runImpact: async ({ id }) => {
      const d: ImpactRes = await mutate(IMPACT, { id });
      const r = d.decisionImpact;
      return { summary: r.summary, docs: r.docs ?? [] };
    },
    /* The scan is a workflow run: `startDecisionScan` opens it against the
       "Decision scan" flow and answers with the run id, so the page can follow
       it the same way the Facts page follows its own. It used to be a fire-
       and-forget mutation behind a link. */
    scan: async () => {
      const d = await mutate(START_SCAN);
      return readRun(String(d.startDecisionScan));
    },
    scanProgress: (id: string) => readRun(id),
  };
}
