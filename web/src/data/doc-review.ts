/* Doc Review adapter — one document and everything the five review panels say
 * about it.
 *
 * The route is `/knowledge/doc`, with the document in `?id=`. Four per-document
 * root fields answer in one document, so the whole workspace gets one loading
 * state instead of five racing panels. */

import { useSearchParams } from "react-router-dom";
import type { DocReviewData, ReviewDoc } from "@mari-design/components/pages/DocReviewPage";
import type { DocRevision } from "@mari-design/components/features/DocReviewOutlinePanel";
import type { EditorFinding } from "@mari-design/components/features/DocReviewEditor";
import type { DocChange } from "@mari-design/components/features/DocReviewChangeQueue";
import type { DocClaim, DocFinding } from "@mari-design/components/features/DocReviewFindingsPanel";
import { useQuery } from "../lib/api";
import type { PageData } from "./types";

/* ── query ──────────────────────────────────────────────────────────────── */

const QUERY = `query DocReview($id: Int!) {
  document(id: $id) { id title body author date }
  revisions(documentId: $id) { id actor verb at }
  findings(documentId: $id) { id kind severity text note }
  changes(documentId: $id) { id original replacement reason status }
}`;

type Res = {
  document: { id: number; title: string; body: string; author: string; date: string } | null;
  revisions: { id: number; actor: string; verb: string; at: string }[];
  findings: { id: number; kind: string; severity: string; text: string; note: string }[];
  changes: { id: number; original: string; replacement: string; reason: string; status: string }[];
};

/* ── mapping helpers ────────────────────────────────────────────────────── */

/* `changes.status` is a three-value column, but the queue only draws three
   states and a fourth would render an unlabelled row. Anything else reads as
   still-pending — the one reading that cannot claim a decision was made. */
const CHANGE_STATE: Record<string, DocChange["state"]> = {
  pending: "pending",
  accepted: "accepted",
  rejected: "rejected",
};

/** The refine panel's tally. Counted off the findings we already have, so the
 *  number beside a severity can never disagree with the list under it. */
function tally(findings: Res["findings"]): ReviewDoc["refine"] {
  const t = { errorN: 0, warnN: 0, advisoryN: 0 };
  for (const f of findings) {
    const s = f.severity?.toLowerCase();
    if (s === "error") t.errorN++;
    else if (s === "warn" || s === "warning") t.warnN++;
    else t.advisoryN++;
  }
  return t;
}

/* ── mapper ─────────────────────────────────────────────────────────────── */

const EMPTY_DOC: ReviewDoc = {
  outlineBody: "", revisions: [], editorBody: "", editorFindings: [],
  refine: { errorN: 0, warnN: 0, advisoryN: 0 },
  changes: [], changeBody: "", findings: [], claims: [],
};

/** A document the API has not answered for yet — or a route with no `?id=`.
 *  Every collection is genuinely empty, which is what makes the page's own
 *  `isEmpty` fire for the same reason it fires on a brand-new document. */
export const EMPTY: DocReviewData = {
  title: "", subtitle: "", save: "saved", pane: "workspace", doc: EMPTY_DOC,
};

export function mapDocReview(res: Res | null): DocReviewData {
  const d = res?.document;
  if (!res || !d) return EMPTY;

  const findings = (res.findings ?? []).map<DocFinding>((f) => ({
    id: f.id, kind: f.kind, severity: f.severity, text: f.text, note: f.note,
  }));

  return {
    title: d.title,
    // Owner and last-update line. Both are document columns; neither is prose.
    subtitle: [d.author, d.date].filter(Boolean).join(" · "),
    // Save lifecycle belongs to a mutation this page does not run yet: a
    // freshly loaded document is exactly what the server has.
    save: "saved",
    // All three panes at once. Deep-linking a single pane is a route this app
    // does not have, so claiming one would be inventing navigation.
    pane: "workspace",
    doc: {
      // One body, rendered three ways: the outline is derived from its
      // headings, the editor renders it, the change queue diffs against it.
      outlineBody: d.body,
      editorBody: d.body,
      changeBody: d.body,
      revisions: (res.revisions ?? []).map<DocRevision>((r) => ({
        id: r.id, actor: r.actor, verb: r.verb, at: r.at,
      })),
      // The editor's inline marks and the findings panel's list are the same
      // rows; the two panels just draw them differently.
      editorFindings: findings as EditorFinding[],
      findings,
      refine: tally(res.findings ?? []),
      changes: (res.changes ?? []).map<DocChange>((c) => ({
        id: c.id,
        original: c.original,
        proposed: c.replacement,
        rule: c.reason,
        state: CHANGE_STATE[c.status?.toLowerCase()] ?? "pending",
      })),
      // Extracted claims: `facts` is a workspace-wide table with no document
      // foreign key, so there is no honest way to say which claims came from
      // THIS document. Left empty until the backend records that link (see
      // the report) rather than showing another document's claims here.
      claims: [] as DocClaim[],
    },
  };
}

/* ── adapter ────────────────────────────────────────────────────────────── */

export function useDocReview(): PageData<DocReviewData> {
  const [params] = useSearchParams();
  const id = Number(params.get("id"));
  const valid = Number.isInteger(id) && id > 0;

  // `skip` is not a thing useQuery has; an id of 0 matches no document and the
  // resolver answers null, which is the same empty page as no id at all.
  const q = useQuery<DocReviewData>(QUERY, {
    variables: { id: valid ? id : 0 },
    map: mapDocReview,
  });

  return {
    data: q.data ?? EMPTY,
    loading: valid ? q.loading : false,
    error: q.error ? (q.errorText ?? "This document is temporarily unavailable.") : null,
  };
}
