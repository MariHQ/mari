/* Facts actions — the write half of the fact ledger.
 *
 * One handler per intent the page offers. Each throws on failure and the
 * control that called it shows the server's own message, so a rejected write
 * is exactly as visible as a failed read.
 *
 * Two intents stay unwired, and so undrawn: `editFact` and `retireFact`. The
 * fact ledger has `addFact` and `verifyFact` and nothing else — no mutation
 * rewrites a claim in place, and none retires one — so a row offers no Edit
 * and no Retire rather than a control that would report success and change
 * nothing. */

import type { FactScan, FactsActions } from "@mari-design/components/pages/FactsPage";
import type { RunStatus } from "@mari-design/components/workflow/RunHistory";
import { gqlResult } from "../../lib/api";
import { mutate, type ActionContext } from "../actions";
import { factsChanged } from "../facts";

// `rows` and `stats` are JSON scalars on the run, so they arrive whole.
const RUN_QUERY = `query($id: Int!) {
  workflowRun(id: $id) { id number workflowName status progress stats rows }
}`;

type RunRes = {
  workflowRun: {
    id: number; number: number; workflowName: string; status: string; progress: number;
    stats: { facts?: number } | null;
    rows: { step?: string; status?: string; detail?: string; duration?: string }[] | null;
  } | null;
};

/* The engine's step vocabulary is the library's, one word for one word. An
 * unknown word would render as a chip the console has no meaning for, so it
 * reads as still running rather than as a state the run never reported. */
const RUN_STATUS = new Set<RunStatus>(["passed", "running", "waiting", "failed", "skipped", "pending"]);
const asStatus = (s: string): RunStatus => (RUN_STATUS.has(s as RunStatus) ? (s as RunStatus) : "running");
const invalidatedRuns = new Set<string>();

function invalidateFactsOnce(id: string, status: RunStatus) {
  if ((status === "running" || status === "pending") || invalidatedRuns.has(id)) return;
  invalidatedRuns.add(id);
  factsChanged();
  if (invalidatedRuns.size > 200) invalidatedRuns.delete(invalidatedRuns.values().next().value!);
}

function mapRun(res: RunRes | null, id: string): FactScan {
  const run = res?.workflowRun;
  if (!run) throw new Error("That scan is no longer on record.");
  const status = asStatus(run.status);
  // A run that has stopped has landed whatever it was going to land, so the
  // ledger under the page re-reads instead of showing yesterday's rows.
  invalidateFactsOnce(id, status);
  return {
    id,
    label: `${run.workflowName} · run #${run.number}`,
    status,
    progress: run.progress ?? 0,
    steps: (run.rows ?? []).map((r) => ({
      step: r.step ?? "step", status: asStatus(r.status ?? ""), detail: r.detail ?? "", duration: r.duration,
    })),
    // Only a run that scanned reports a count; until then the page says nothing
    // about how many claims landed.
    added: typeof run.stats?.facts === "number" ? run.stats.facts : null,
  };
}

async function readRun(id: string): Promise<FactScan> {
  const r = await gqlResult<RunRes>(RUN_QUERY, { id: Number(id) });
  if (!r.ok) throw new Error(r.error);
  return mapRun(r.data, id);
}

export function factsActions({ currentUserName }: ActionContext): FactsActions {
  return {
    verifyFact: async (id: number) => {
      await mutate("mutation($id: Int!) { verifyFact(id: $id) }", { id });
    },
    addFact: async ({ claim, source, owner }) => {
      // `owner` defaults server-side, so an unowned claim is still accepted
      // rather than being rejected for a field the form left blank.
      await mutate(
        "mutation($claim: String!, $source: String!, $owner: String!) { addFact(claim: $claim, source: $source, owner: $owner) }",
        { claim, source, owner: owner || currentUserName },
      );
      // The drawer closes on success; without this the claim it just captured
      // would not appear in the table behind it until the next visit.
      factsChanged();
    },
    /* The scan is a workflow run: `startFactScan` opens it against the "Fact
     * scan" flow and the engine executes the steps on a background thread, so
     * this returns as soon as the run exists and the page follows it. */
    scanFacts: async () => {
      const d = await mutate("mutation { startFactScan }");
      return readRun(String(d.startFactScan));
    },
    scanProgress: (id: string) => readRun(id),
    createReviewTask: async (fact) => {
      // `factCheck` is the fact-check *detector* and takes a document, not a
      // claim, so re-verification is a task on the ledger row: the same
      // createTask the Tasks page reads back.
      await mutate(
        `mutation(
          $title: String!, $kind: String!, $kindLabel: String!, $assignee: String!,
          $subjectType: String!, $subjectId: String!, $subjectTitle: String!, $subjectHref: String!
        ) {
          createTask(
            title: $title, kind: $kind, kindLabel: $kindLabel, assignee: $assignee,
            subjectType: $subjectType, subjectId: $subjectId, subjectTitle: $subjectTitle, subjectHref: $subjectHref
          )
        }`,
        {
          title: `Re-verify: ${fact.claim}`,
          kind: "factcheck",
          kindLabel: "Fact check",
          assignee: fact.owner || currentUserName,
          subjectType: "fact",
          subjectId: String(fact.id),
          subjectTitle: fact.claim,
          subjectHref: `/facts?fact=${fact.id}`,
        },
      );
    },
  };
}
