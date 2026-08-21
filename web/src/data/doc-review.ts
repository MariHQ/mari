/* Doc Review adapter — one document and everything the five review panels say
 * about it.
 *
 * The route is `/knowledge/doc`, with the document in `?id=`. Four per-document
 * root fields answer in one document, so the whole workspace gets one loading
 * state instead of five racing panels. */

import { useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import type { DocReviewData, ReviewDoc, ReviewPane } from "@mari-design/components/pages/DocReviewPage";
import type { DocRevision } from "@mari-design/components/features/DocReviewOutlinePanel";
import type { EditorFinding } from "@mari-design/components/features/DocReviewEditor";
import type { DocChange } from "@mari-design/components/features/DocReviewChangeQueue";
import type { DocClaim, DocFinding } from "@mari-design/components/features/DocReviewFindingsPanel";
import { useQuery } from "../lib/api";
import type { PageData } from "./types";

/* ── query ──────────────────────────────────────────────────────────────── */

const QUERY = `query DocReview($id: Int!) {
  document(id: $id) { id title body author date tags watched }
  revisions(documentId: $id) { id actor verb at }
  findings(documentId: $id) { id kind severity text note }
  changes(documentId: $id) { id original replacement reason status }
  claims(documentId: $id) { id claim source status verified }
}`;

type Res = {
  document: {
    id: number; title: string; body: string; author: string; date: string;
    tags: string[]; watched: boolean;
  } | null;
  revisions: { id: number; actor: string; verb: string; at: string }[];
  findings: { id: number; kind: string; severity: string; text: string; note: string }[];
  changes: { id: number; original: string; replacement: string; reason: string; status: string }[];
  claims: { id: number; claim: string; source: string; status: string; verified: string }[];
};

/* ── mapping helpers ────────────────────────────────────────────────────── */

/* The five single-pane views are deep links: `/knowledge/doc?id=7&pane=changes`
   opens the change queue full-width. They were unreachable because the adapter
   said "workspace" no matter what the URL held. Anything else in `?pane=` is
   not a pane, so the route falls back to the whole workspace rather than
   rendering nothing. */
const PANES = new Set<ReviewPane>(["workspace", "outline", "findings"]);

function paneOf(raw: string | null): ReviewPane {
  return raw && PANES.has(raw as ReviewPane) ? (raw as ReviewPane) : "workspace";
}

/* The bottom tab strip is deep-linkable the same way: `?tab=findings` opens the
   fact check under the editor instead of the change queue. `BottomTab` is not
   exported from the page module, so it is read off the prop it belongs to —
   which also means a new tab in the library cannot silently go unroutable here.

   Anything else in `?tab=` names no tab, and the answer is `undefined` rather
   than a default: the page already has its own opening tab, and restating it
   here would be this adapter inventing a preference nobody expressed. */
type BottomTab = NonNullable<DocReviewData["bottomTab"]>;
const BOTTOM_TABS = new Set<BottomTab>(["changes", "findings"]);

function bottomTabOf(raw: string | null): BottomTab | undefined {
  return raw && BOTTOM_TABS.has(raw as BottomTab) ? (raw as BottomTab) : undefined;
}

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

export function mapDocReview(res: Res | null, pane: ReviewPane = "workspace"): DocReviewData {
  const d = res?.document;
  if (!res || !d) return { ...EMPTY, pane };

  const findings = (res.findings ?? []).map<DocFinding>((f) => ({
    id: f.id, kind: f.kind, severity: f.severity, text: f.text, note: f.note,
  }));

  return {
    title: d.title,
    // Owner and last-update line. Both are document columns; neither is prose.
    subtitle: [d.author, d.date].filter(Boolean).join(" · "),
    // Deprecated compatibility field. Synced source records are read-only.
    save: "saved",
    // Which pane the URL asked for. The whole workspace unless `?pane=` names
    // one of the five single-pane views.
    pane,
    // The document's own tags. Not a guess: the same `tags` rows the Knowledge
    // library reads. A document nobody has tagged carries none, and the header
    // then draws no chips.
    tags: d.tags ?? [],
    // Whether this reader watches the document — a real `watches` row, which is
    // also what `toggleWatch` writes. The page owns the state from here.
    watched: d.watched,
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
      // Extracted claims, by key: `facts.document_id` records the document the
      // extractor actually read (see server/mutations_knowledge.py). It is not
      // matched on `facts.source`, which is a free-text label. Claims landed
      // before that column existed carry no document and are absent here —
      // an empty list means nothing has been mined from this document yet,
      // never that the attribution was too hard to work out.
      // `verified` is the ISO date the API serves, or "" for never verified.
      claims: (res.claims ?? []).map<DocClaim>((c) => ({
        claim: c.claim, source: c.source, status: c.status, verified: c.verified,
      })),
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
  const pane = paneOf(params.get("pane"));
  const bottomTab = bottomTabOf(params.get("tab"));

  const q = useQuery<DocReviewData>(QUERY, {
    variables: { id: valid ? id : 0 },
    map: mapDocReview,
  });
  const data = useMemo(() => ({ ...(q.data ?? EMPTY), pane, bottomTab }), [q.data, pane, bottomTab]);

  return {
    // The pane and the bottom tab are applied here rather than inside the
    // mapper: the query cache is keyed on the GraphQL variables, and neither of
    // these is one of them, so a mapped-in value would be frozen at whatever
    // the first visit asked for.
    data,
    loading: valid ? q.loading : false,
    error: q.error ? (q.errorText ?? "This document is temporarily unavailable.") : null,
  };
}
