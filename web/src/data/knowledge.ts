/* Knowledge browser adapter over the hybrid-search root field.
 *
 * Everything the reader chose lives in the URL: the query in `?q=`, how many
 * results have been asked for in `?k=`, and which one is being inspected in
 * `?doc=`. That is what makes a Knowledge search shareable and reload-proof —
 * and, more importantly, it is what lets the query reach the server at all.
 * The browser used to own the search box in local state, so `q` was always ""
 * and typing filtered the first 40 rows of the corpus. */

import { useSearchParams } from "react-router-dom";
import type { KnowledgeData } from "@mari-design/components/pages/KnowledgePage";
import type { KnowledgeResult } from "@mari-design/components/features/KnowledgeBrowser";
import type { KnowledgeDoc } from "@mari-design/components/features/KnowledgeInspector";
import { useQuery } from "../lib/api";
import type { PageData } from "./types";

/** Results fetched on first load, and the size of each "show more" step. */
export const PAGE = 40;

// An empty query is a real request: `search` falls back to most-recently-
// updated, which is what an unsearched browser should show. `searchTotal`
// answers for the same query with no limit, so the feed can say how many of
// how many it is showing instead of calling the page the corpus.
const QUERY = `query Knowledge($q: String!, $k: Int!) {
  search(query: $q, k: $k) { id source title snippet kind author authorInitials date tags }
  searchTotal(query: $q)
}`;

type Res = {
  search: {
    id: number; source: string; title: string; snippet: string; kind: string;
    author: string; authorInitials: string; date: string; tags: string[];
  }[];
  searchTotal: number;
};

/* documents.kind is open-ended (page, commit, pr, issue, answer, decision, …);
   the browser draws three. Everything that is not a pull request or a chat
   thread is a page — which is what those kinds are. */
const KIND: Record<string, KnowledgeResult["kind"]> = {
  pr: "pr",
  thread: "thread",
};

/** The feed and the number above it, from one response — so the count can
 *  never describe a different result set from the rows under it. */
export function mapSearch(res: Res): { results: KnowledgeResult[]; total: number } {
  return { results: mapKnowledge(res), total: res.searchTotal ?? 0 };
}

export function mapKnowledge(res: Res): KnowledgeResult[] {
  return (res.search ?? []).map<KnowledgeResult>((d) => ({
    id: String(d.id),
    kind: KIND[d.kind] ?? "page",
    source: d.source,
    title: d.title,
    snippet: d.snippet,
    author: d.author,
    date: d.date,
    tags: d.tags ?? [],
    // status, messageCount and participantCount are optional and have no
    // backing column. Omitted, so the row simply does not draw them.
  }));
}

/* The inspected document. A second query, made only once a row is selected —
   which is why it is not folded into the search above. */
const DOC_QUERY = `query KnowledgeDoc($id: Int!) {
  document(id: $id) { id source title snippet kind author date tags watched }
  revisions(documentId: $id) { id actor verb at }
  relatedDocuments(documentId: $id) { id source title rel direction }
}`;

type DocRes = {
  document: {
    id: number; source: string; title: string; snippet: string; kind: string;
    author: string; date: string; tags: string[]; watched: boolean;
  } | null;
  revisions: { id: number; actor: string; verb: string; at: string }[];
  relatedDocuments: { id: number; source: string; title: string; rel: string; direction: string }[];
};

/* The rail draws lineage rows for the relations the graph has a colour and a
   direction label for. An edge the classifier spells some other way becomes a
   plain related document rather than a relation it is not. */
const REL: Record<string, "references" | "discussed" | "derived" | "translates" | "contradicts" | "similar"> = {
  references: "references",
  reference: "references",
  links_to: "references",
  discussed: "discussed",
  derived: "derived",
  translates: "translates",
  contradicts: "contradicts",
  similar: "similar",
};

export function mapKnowledgeDoc(res: DocRes | null): KnowledgeDoc | null {
  const d = res?.document;
  if (!d) return null;
  const related = res?.relatedDocuments ?? [];
  return {
    // The same id the result card carries, because it is the same row: the
    // rail can only describe the card you picked if the two agree on the key.
    id: String(d.id),
    title: d.title,
    source: d.source,
    kind: d.kind,
    owner: d.author,
    updated: d.date,
    // A Slack document is a thread chunk, and the rail draws it differently
    // (no tags, a decision-excerpt note). The message count has no column, so
    // it is left off rather than guessed at.
    slack: d.source === "slack",
    summary: d.snippet,
    tags: d.tags ?? [],
    // `facts` is a workspace-wide table with no document foreign key, so there
    // is no honest way to say which claims came from THIS document. Left
    // UNDEFINED rather than empty — the difference is load-bearing: the rail
    // draws no Verified facts section at all instead of a section that is
    // permanently empty on every document. It becomes an array the day
    // `facts` records the document it was mined from.
    related: related.map((r) => ({ source: r.source, title: r.title })),
    // The same edges, with the relation and direction the graph records, so
    // the rail's lineage preview shows real connections rather than a cycle of
    // invented relations derived from the related list.
    lineage: related
      .filter((r) => REL[r.rel])
      .map((r) => ({
        rel: REL[r.rel],
        dir: r.direction === "in" ? ("in" as const) : ("out" as const),
        title: r.title,
        source: r.source,
      })),
    timeline: (res?.revisions ?? []).map((r) => ({ at: r.at, actor: r.actor, verb: r.verb })),
    watched: d.watched,
  };
}

/** How many results the URL is asking for. Clamped, because `?k=` is typed by
 *  whoever holds the link: a negative or absurd page is not a request. */
function pageSize(raw: string | null): number {
  const k = Number(raw);
  if (!Number.isInteger(k) || k < PAGE) return PAGE;
  return Math.min(k, 1000);
}

export function useKnowledge(): PageData<KnowledgeData> {
  /* Query, page size and selection are all ROUTE state. The browser used to
     keep the query to itself, so the hybrid-search backend was unreachable
     from the UI; in `?q=` it is the thing the server is actually asked. */
  const [params] = useSearchParams();
  const query = params.get("q") ?? "";
  const k = pageSize(params.get("k"));

  const q = useQuery<{ results: KnowledgeResult[]; total: number }>(QUERY, {
    variables: { q: query, k }, map: mapSearch,
  });

  const id = Number(params.get("doc"));
  const selected = Number.isInteger(id) && id > 0;

  // Hooks cannot be conditional, and an id of 0 matches no document: the
  // resolver answers null, which is the same "nothing selected" as no id.
  const docQ = useQuery<KnowledgeDoc | null>(DOC_QUERY, {
    variables: { id: selected ? id : 0 },
    map: mapKnowledgeDoc,
  });

  return {
    data: {
      results: q.data?.results ?? [],
      doc: docQ.data ?? null,
      query,
      // The corpus's answer, not the page's length. Undefined until the query
      // answers, so the feed never states a total nobody has counted.
      total: q.data?.total,
      /* The facet rail (source / type / owner / status / freshness) still
         counts and filters the LOADED results. Those counts are server-
         answerable in principle — they are group-bys over the same match set —
         but `search` takes no filter arguments and there is no faceted-count
         field to read them from, so the browser is told how many results its
         counts cover (`total`) and says so rather than passing a page count
         off as a corpus count. Fixing it properly is a filtered `search` plus
         a facet-count field (P-KN-4). */
    },
    // Only the search drives the page skeleton. Selecting a row must not blank
    // the feed you selected it from while the rail loads.
    loading: q.loading,
    // The page has one error surface for the feed and the rail, so a failed
    // document read says so out loud instead of leaving the rail claiming
    // nothing was selected while a document plainly is.
    error: q.error
      ? (q.errorText ?? "Search is temporarily unavailable.")
      : selected && docQ.error
        ? (docQ.errorText ?? "That document is temporarily unavailable.")
        : null,
  };
}
