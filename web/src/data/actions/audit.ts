/* Repo-audit actions — run the scan, then work the findings list down.
 *
 * `fixAllAuditFindings` is keyed by run, but the checklist only knows the kind
 * it is looking at: the page renders the latest run, so the run id is resolved
 * here rather than pushed into the page's data contract. */

import type { AuditActions } from "@mari-design/components/pages/AuditPage";
import { mutate } from "../actions";
import { gqlResult } from "../../lib/api";

async function latestRunId(): Promise<number> {
  const r = await gqlResult<{ auditRuns: { id: number }[] }>("{ auditRuns { id } }");
  if (!r.ok) throw new Error(r.error);
  // auditRuns comes back newest first, and the checklist is showing that run.
  const id = r.data?.auditRuns?.[0]?.id;
  if (id == null) throw new Error("There is no audit run to fix findings on yet.");
  return id;
}

export function auditActions(): AuditActions {
  return {
    runAudit: async (provider: string) => {
      // A workspace that has never audited has no provider on record yet;
      // github is the only repository provider the scanner walks.
      await mutate("mutation($provider: String!) { runRepoAudit(provider: $provider) }",
        { provider: provider || "github" });
    },
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
