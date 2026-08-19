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

const CREATE_TASK = `mutation CreateTask(
  $title: String!, $kind: String!, $kindLabel: String!,
  $subjectType: String!, $subjectId: String!, $subjectTitle: String!, $subjectHref: String!
) {
  createTask(
    title: $title, kind: $kind, kindLabel: $kindLabel,
    subjectType: $subjectType, subjectId: $subjectId, subjectTitle: $subjectTitle, subjectHref: $subjectHref
  )
}`;

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

    /* One task per affected document, filed against the same ledger the Tasks
       page reads. There is no bulk mutation, so they are written one at a time
       and a partial failure throws with the server's own words: the tasks that
       DID land stay landed, and the ledger shows them on the next read.

       `approval` is not a guess — a ratified decision's impact is a document
       someone has to sign off on, which is the kind the board already has a
       pill for. The document's severity rides in the title, because a task
       carries no other field to put it in. */
    createImpactTasks: async ({ id, docs }) => {
      const decision = (await gqlResult<{ decisions: { id: number; statement: string }[] }>(
        `{ decisions { id statement } }`,
      ));
      if (!decision.ok) throw new Error(decision.error);
      const statement = (decision.data?.decisions ?? []).find((d) => d.id === id)?.statement;
      if (!statement) throw new Error("That decision is no longer in the ledger.");
      for (const doc of docs) {
        await mutate(CREATE_TASK, {
          title: `${doc.title}: ${doc.reason || `affected by "${statement}"`}`,
          kind: "approval",
          kindLabel: "Approval",
          subjectType: "decision",
          subjectId: String(id),
          subjectTitle: statement,
          subjectHref: `/decisions?decision=${id}`,
        });
      }
      // The board and the ledger both moved.
      decisionsChanged();
    },
  };
}
