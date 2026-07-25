/* Repo-audit actions — run the scan, then work the findings list down.
 *
 * `fixAllAuditFindings` is keyed by run, but the checklist only knows the kind
 * it is looking at: the page renders one run, so the run id is resolved here
 * rather than pushed into the page's data contract.
 */

import type { AuditActions } from "@mari-design/components/pages/AuditPage";
import type { ScanRun } from "@mari-design/components/features/ScanRunCard";
import { mutate, type ActionContext } from "../actions";
import { gqlResult } from "../../lib/api";

const RUN_QUERY = `{ auditRuns { id repo findings fixed ranAt } }`;

type RunRow = { id: number; repo: string; findings: number; fixed: number; ranAt: string };

async function runs(): Promise<RunRow[]> {
  const r = await gqlResult<{ auditRuns: RunRow[] }>(RUN_QUERY);
  if (!r.ok) throw new Error(r.error);
  return r.data?.auditRuns ?? [];
}

async function latestRunId(): Promise<number> {
  // auditRuns comes back newest first, and the checklist is showing that run.
  const id = (await runs())[0]?.id;
  if (id == null) throw new Error("There is no audit run to fix findings on yet.");
  return id;
}

/* The repo audit is not a workflow run: `runRepoAudit` walks the checked-out
   tree on the request thread and returns only once it has written the run row,
   so by the time the console holds an id the scan has finished. The page shows
   it through the same ScanRunCard as Facts and Decisions, so the three read
   alike — but every field here is read back off the stored run rather than
   sampled from a progress channel that does not exist. No per-step timeline:
   the scanner records none, and the card draws none rather than inventing one. */
function asScanRun(row: RunRow | undefined, id: string): ScanRun {
  if (!row) throw new Error("That audit run is no longer on record.");
  return {
    id,
    label: `Repo audit · ${row.repo}`,
    // A run row only exists once the scan has completed and committed.
    status: "passed",
    progress: 100,
    steps: [],
    added: row.findings,
  };
}

async function readRun(id: string): Promise<ScanRun> {
  const all = await runs();
  return asScanRun(all.find((r) => String(r.id) === id), id);
}

export function auditActions({ navigate }: ActionContext): AuditActions {
  return {
    runAudit: async (provider: string) => {
      // A workspace that has never audited has no provider on record yet;
      // github is the only repository provider the scanner walks.
      const d = await mutate("mutation($provider: String!) { runRepoAudit(provider: $provider) }",
        { provider: provider || "github" });
      const id = d?.runRepoAudit;
      if (typeof id !== "number") throw new Error("The audit ran but reported no run to follow.");
      return readRun(String(id));
    },
    scanProgress: (id: string) => readRun(id),
    // A past run is a route, so the rail's rows are links, and the run on
    // screen survives a reload or a shared URL.
    openRun: (id: string) => navigate(`/audit?run=${encodeURIComponent(id)}`),
    fixFinding: async ({ id, memberName }) => {
      await mutate(
        "mutation($id: Int!, $memberName: String!) { fixAuditFinding(id: $id, memberName: $memberName) }",
        { id, memberName: memberName ?? "" },
      );
    },
    fixAllFindings: async ({ kind }) => {
      // The checklist's section headings are the display vocabulary
      // ("Localization"); `audit_findings.kind` stores the lowercase token, and
      // the server matches on it exactly, so an unmapped kind silently fixes
      // nothing.
      await mutate(
        "mutation($runId: Int!, $kind: String!) { fixAllAuditFindings(runId: $runId, kind: $kind) }",
        { runId: await latestRunId(), kind: kind.toLowerCase() },
      );
    },
    dismissFinding: async (id: number) => {
      await mutate("mutation($id: Int!) { dismissAuditFinding(id: $id) }", { id });
    },
  };
}
