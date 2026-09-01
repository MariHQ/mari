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

import type { FactIntelligence, FactLlmBudget, FactScan, FactsActions, FactSemanticImpactLink } from "@mari-design/components/pages/FactsPage";
import type { RunStatus } from "@mari-design/components/workflow/RunHistory";
import { gqlResult } from "../../lib/api";
import { mutate, type ActionContext } from "../actions";
import { factsChanged } from "../facts";
import { requestFactScanConfiguration } from "../../components/FactScanConfiguration";

// `rows` and `stats` are JSON scalars on the run, so they arrive whole.
const RUN_QUERY = `query($id: Int!) {
  workflowRun(id: $id) { id number workflowName status progress stats rows }
  factExtractionCandidates(runId: $id) {
    id runId documentId documentTitle claim source evidence confidence reviewStatus
    reviewKind reviewReason reviewer reviewedAt publishedFactId impactScore highImpact
    semanticLinks { targetType targetId relation similarity targetLabel targetUpdatedAt observedAt }
  }
  factLlmBudgets(runId: $id) {
    stage purpose model maxCalls maxInputTokens maxOutputTokens
    callsUsed inputTokens outputTokens status
  }
  factRunIntelligence(runId: $id) {
    candidateId structuredClaim adjudication validFrom validTo
    components { role text }
    relations { targetClaim relation exactScore approximateScore decisionKind rationale }
    evidenceGroups {
      verdict sufficient confidence rationale decisionKind
      spans { documentTitle quote role similarity }
    }
    clusters { label stableKey labelKind membershipScore }
  }
}`;

type RunRes = {
  workflowRun: {
    id: number; number: number; workflowName: string; status: string; progress: number;
    stats: { facts?: number } | null;
    rows: { step?: string; status?: string; detail?: string; duration?: string }[] | null;
  } | null;
  factExtractionCandidates: {
    id: number; runId: number; documentId: number | null; documentTitle: string;
    claim: string; source: string; evidence: string; confidence: number;
    reviewStatus: string; reviewKind: string; reviewReason: string; reviewer: string;
    reviewedAt: string; publishedFactId: number | null;
    impactScore: number; highImpact: boolean;
    semanticLinks: {
      targetType: "fact" | "document"; targetId: number; relation: string;
      similarity: number; targetLabel: string; targetUpdatedAt: string; observedAt: string;
    }[];
  }[];
  factLlmBudgets: FactLlmBudget[];
  factRunIntelligence: (Omit<FactIntelligence, "relations"> & {
    candidateId: number | null;
    relations: { targetClaim: string; relation: string; exactScore: number | null;
      approximateScore: number | null; decisionKind: string; rationale: string }[];
  })[];
};

/* The engine's step vocabulary is the library's, one word for one word. An
 * unknown word would render as a chip the console has no meaning for, so it
 * reads as still running rather than as a state the run never reported. */
const RUN_STATUS = new Set<RunStatus>(["passed", "running", "waiting", "failed", "skipped", "pending"]);
const asStatus = (s: string): RunStatus => (RUN_STATUS.has(s as RunStatus) ? (s as RunStatus) : "running");
const invalidatedRuns = new Set<string>();

function invalidateFactsOnce(id: string, status: RunStatus) {
  if ((status === "running" || status === "pending" || status === "waiting") || invalidatedRuns.has(id)) return;
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
  const intelligence = new Map((res?.factRunIntelligence ?? [])
    .filter((row) => row.candidateId !== null)
    .map((row) => [row.candidateId!, {
      ...row,
      relations: row.relations.map((relation) => ({
        targetClaim: relation.targetClaim, relation: relation.relation,
        similarity: relation.exactScore ?? relation.approximateScore,
        decisionKind: relation.decisionKind, rationale: relation.rationale,
      })),
    }]));
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
    llmBudgets: res?.factLlmBudgets ?? [],
    candidates: (res?.factExtractionCandidates ?? []).map((candidate) => ({
      id: candidate.id,
      documentTitle: candidate.documentTitle,
      claim: candidate.claim,
      evidence: candidate.evidence,
      confidence: candidate.confidence,
      status: candidate.reviewStatus as "pending" | "accepted" | "rejected",
      reviewReason: candidate.reviewReason,
      reviewer: candidate.reviewer,
      impactScore: candidate.impactScore,
      highImpact: candidate.highImpact,
      semanticLinks: candidate.semanticLinks ?? [],
      intelligence: intelligence.get(candidate.id),
    })).sort((a, b) => Number(b.highImpact) - Number(a.highImpact) || b.impactScore - a.impactScore),
  };
}

async function readRun(id: string): Promise<FactScan> {
  const r = await gqlResult<RunRes>(RUN_QUERY, { id: Number(id) });
  if (!r.ok) throw new Error(r.error);
  return mapRun(r.data, id);
}

async function latestRun(): Promise<FactScan | null> {
  const workflows = await gqlResult<{
    workflows: { id: number; nodes: { kind?: string }[] }[];
  }>("{ workflows { id nodes } }");
  if (!workflows.ok) throw new Error(workflows.error);
  const workflow = workflows.data?.workflows.find((row) =>
    (row.nodes ?? []).some((node) => node.kind === "scan_facts"));
  if (!workflow) return null;
  const runs = await gqlResult<{
    latestWorkflowRun: { id: number; status: string } | null;
  }>("query($id: Int!) { latestWorkflowRun(workflowId: $id) { id status } }", { id: workflow.id });
  if (!runs.ok) throw new Error(runs.error);
  const latest = runs.data?.latestWorkflowRun;
  return latest ? readRun(String(latest.id)) : null;
}

export function factsActions({ currentUserName }: ActionContext): FactsActions {
  return {
    verifyFact: async (id: number) => {
      await mutate("mutation($id: Int!) { verifyFact(id: $id) }", { id });
    },
    retireFact: async (id: number) => {
      await mutate(
        "mutation($id: Int!) { invalidateFact(id: $id, reason: \"Invalidated from the fact ledger\") }",
        { id },
      );
      factsChanged();
    },
    inspectFactImpact: async (id: number): Promise<FactSemanticImpactLink[]> => {
      const result = await gqlResult<{ factImpactPreview: null | { items: {
        impactKind: string; targetType: string; targetId: string; targetLabel: string;
        dependencyType: string; similarity: number | null;
      }[] }; factSemanticLinks: FactSemanticImpactLink[] }>(
        `query($id: Int!) {
          factImpactPreview(factId: $id) {
            items { impactKind targetType targetId targetLabel dependencyType similarity }
          }
          factSemanticLinks(factId: $id) {
            targetType targetId relation similarity targetLabel targetUpdatedAt observedAt
          }
        }`,
        { id },
      );
      if (!result.ok) throw new Error(result.error);
      const items = result.data?.factImpactPreview?.items;
      if (!items?.length) return result.data?.factSemanticLinks ?? [];
      return items.map((item) => ({
        targetType: item.targetType, targetId: item.targetId,
        relation: item.impactKind === "possible" ? "embedding neighbor" : item.dependencyType,
        similarity: item.similarity ?? (item.impactKind === "direct" ? 1 : .75),
        targetLabel: item.targetLabel, targetUpdatedAt: "", observedAt: "",
      }));
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
      const config = await requestFactScanConfiguration();
      if (!config) throw new Error("Fact scan cancelled.");
      const d = await mutate(
        "mutation($config: JSON) { startFactScan(config: $config) }",
        { config },
      );
      return readRun(String(d.startFactScan));
    },
    /* Retry after a failure reuses the configuration the workflow already
     * carries: startFactScan without a config runs the stored pipeline, and
     * the failed documents were never marked scanned, so the rotation picks
     * them straight back up. */
    retryFactScan: async () => {
      const d = await mutate("mutation { startFactScan }");
      return readRun(String(d.startFactScan));
    },
    scanProgress: (id: string) => readRun(id),
    latestFactScan: latestRun,
    reviewFactCandidate: async (runId, candidateId, accept, reason = "") => {
      await mutate(
        "mutation($id: Int!, $accept: Boolean!, $reason: String!) { reviewFactCandidate(id: $id, accept: $accept, reason: $reason) }",
        { id: candidateId, accept, reason },
      );
      return readRun(runId);
    },
    completeFactReview: async (runId) => {
      const data = await mutate(
        "mutation($runId: Int!) { approveRun(runId: $runId) }",
        { runId: Number(runId) },
      );
      if (!data.approveRun) throw new Error("Review every candidate before continuing the workflow.");
      return readRun(runId);
    },
    dismissFactScan: async (runId) => {
      const data = await mutate(
        "mutation($runId: Int!) { dismissWorkflowRun(runId: $runId) }",
        { runId: Number(runId) },
      );
      if (!data.dismissWorkflowRun) throw new Error("The workflow run could not be dismissed.");
    },
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
