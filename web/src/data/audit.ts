/* Repo-audit adapter: the latest run plus its findings. */

import type { AuditData, AuditRun } from "@mari-design/components/pages/AuditPage";
import type { AuditFinding } from "@mari-design/components/features/AuditFindingsChecklist";
import { useQuery } from "../lib/api";
import type { PageData } from "./types";

const QUERY = `{
  auditRuns { id provider repo findings fixed ranAt }
  auditFindings { id runId kind title detail fixAction fixPayload status }
  members { id name }
}`;

type Res = {
  auditRuns: { id: number; provider: string; repo: string; findings: number; fixed: number; ranAt: string }[];
  auditFindings: {
    id: number; runId: number; kind: string; title: string; detail: string;
    fixAction: string; fixPayload: { tag?: string; suggest?: string } | null; status: string;
  }[];
  members: { id: number; name: string }[];
};

/* The checklist draws a fixed set of categories and offers a fixed set of
   one-click fixes. A finding whose kind or action this build does not know
   would render an unlabelled row with a dead button, so it is dropped —
   `repoaudit.py` and the library ship separately. */
const KINDS = new Set(["Localization", "Tags", "Authorship", "Coverage", "Hygiene"]);
const ACTIONS = new Set(["apply_tag", "invite_member", "translation_task", "link_translation", "ingest", "hygiene_task"]);
const STATUSES = new Set(["open", "fixed", "dismissed"]);

/** What a repo audit looks at. Copy, not data — it describes the checker. */
const SCANS = [
  "Untranslated pages against the workspace's languages",
  "Documents with no owner or an unmapped git author",
  "Tag coverage against the tag definitions",
  "Stale pages past the freshness window",
  "Broken cross-references and orphaned documents",
];

export function mapFindings(res: Res, runId: number | null): AuditFinding[] {
  return (res.auditFindings ?? [])
    .filter((f) => (runId === null || f.runId === runId)
      && KINDS.has(f.kind) && ACTIONS.has(f.fixAction) && STATUSES.has(f.status))
    .map<AuditFinding>((f) => ({
      id: f.id,
      kind: f.kind as AuditFinding["kind"],
      title: f.title,
      detail: f.detail,
      fixAction: f.fixAction as AuditFinding["fixAction"],
      fixPayload: f.fixPayload,
      status: f.status as AuditFinding["status"],
    }));
}

export const EMPTY: AuditData = {
  repo: "", provider: "", ranAt: "", summary: "", findings: [], members: [],
  banner: null, history: [], scans: SCANS, extras: null,
};

/** Pure: the whole response → everything the page renders. */
export function buildAudit(res: Res | null): AuditData {
  if (!res) return EMPTY;
  // auditRuns comes back newest first.
  const latest = res.auditRuns?.[0] ?? null;
  return {
    // Empty repo is what makes the page's "connect a repository" state true,
    // so a workspace that has never run an audit must land here honestly.
    repo: latest?.repo ?? "",
    provider: latest?.provider ?? "",
    ranAt: latest?.ranAt ?? "",
    summary: latest
      ? `${latest.findings} finding${latest.findings === 1 ? "" : "s"}, ${latest.fixed} fixed.`
      : "",
    findings: mapFindings(res, latest?.id ?? null),
    members: res.members ?? [],
    banner: null,
    history: (res.auditRuns ?? []).map<AuditRun>((r) => ({
      label: r.repo,
      detail: `${r.findings} found · ${r.fixed} fixed`,
      current: r.id === latest?.id,
    })),
    scans: SCANS,
    extras: null,
  };
}

export function useAudit(): PageData<AuditData> {
  const q = useQuery<Res>(QUERY, { map: (d: Res) => d });
  return {
    data: buildAudit(q.data),
    loading: q.loading,
    error: q.error ? (q.errorText ?? "The audit is temporarily unavailable.") : null,
  };
}
