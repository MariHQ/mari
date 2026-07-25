/* Repo-audit adapter: one run plus its findings.
 *
 * Which run is a route — `/audit` is the latest, `/audit?run=<id>` is the one
 * the history rail was clicked on — so a past run is a place, shareable and
 * reload-proof, rather than a click the page forgets. */

import { useSearchParams } from "react-router-dom";
import type { AuditData, AuditRun } from "@mari-design/components/pages/AuditPage";
import type { AuditFinding } from "@mari-design/components/features/AuditFindingsChecklist";
import { useQuery } from "../lib/api";
import type { PageData } from "./types";

/* `run` names which run's findings to fetch: null asks the server for the
   latest, which is exactly what `/audit` with no run in the route means. The
   history rail is fetched whole regardless, so opening an older run does not
   shorten the list it was opened from. */
const QUERY = `query Audit($run: Int) {
  auditRuns { id provider repo findings fixed ranAt }
  auditFindings(runId: $run) { id runId kind title detail fixAction fixPayload status }
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
/* The API stores these lowercase ("localization"); the library's `kind` union
   is Capitalised because it is also the section heading. Matching on the
   capitalised form silently dropped EVERY finding, so the checklist rendered
   empty against a repo audit that had found five things. Compare case-folded,
   and map to the display form once. */
const KIND_OF: Record<string, AuditFinding["kind"]> = {
  localization: "Localization", tags: "Tags", authorship: "Authorship",
  coverage: "Coverage", hygiene: "Hygiene",
};
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
      && KIND_OF[String(f.kind).toLowerCase()] && ACTIONS.has(f.fixAction) && STATUSES.has(f.status))
    .map<AuditFinding>((f) => ({
      id: f.id,
      kind: KIND_OF[String(f.kind).toLowerCase()],
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

/** Pure: the whole response → everything the page renders. `askedId` is the
 *  run the route names; absent (or gone from the window the query fetched) it
 *  falls back to the latest, which is what `/audit` on its own means. */
export function buildAudit(res: Res | null, askedId: number | null = null): AuditData {
  if (!res) return EMPTY;
  // auditRuns comes back newest first.
  const runs = res.auditRuns ?? [];
  const latest = (askedId !== null ? runs.find((r) => r.id === askedId) : null) ?? runs[0] ?? null;
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
    history: runs.map<AuditRun>((r) => ({
      // The run's own handle, which is what makes the rail row a button that
      // opens it rather than a hover state over nothing.
      id: String(r.id),
      label: r.repo,
      detail: `${r.findings} found · ${r.fixed} fixed`,
      // ISO, as the server stored it. The rail formats and orders on it.
      ranAt: r.ranAt || undefined,
      current: r.id === latest?.id,
    })),
    scans: SCANS,
    extras: null,
  };
}

export function useAudit(): PageData<AuditData> {
  const [params] = useSearchParams();
  const asked = Number(params.get("run"));
  const askedId = Number.isInteger(asked) && asked > 0 ? asked : null;
  const q = useQuery<Res>(QUERY, { variables: { run: askedId }, map: (d: Res) => d });
  return {
    data: buildAudit(q.data, askedId),
    loading: q.loading,
    error: q.error ? (q.errorText ?? "The audit is temporarily unavailable.") : null,
  };
}
