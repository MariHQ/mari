/* Knowledge browser adapter over the hybrid-search root field. */

import { useSearchParams } from "react-router-dom";
import type { KnowledgeData } from "@mari-design/components/pages/KnowledgePage";
import type { KnowledgeResult } from "@mari-design/components/features/KnowledgeBrowser";
import type { KnowledgeDoc } from "@mari-design/components/features/KnowledgeInspector";
import { useQuery } from "../lib/api";
import type { PageData } from "./types";

// An empty query is a real request: `search` falls back to most-recently-
// updated, which is what an unsearched browser should show.
const QUERY = `query Knowledge($q: String!) {
  search(query: $q, k: 40) { id source title snippet kind author authorInitials date tags }
}`;

type Res = {
  search: {
    id: number; source: string; title: string; snippet: string; kind: string;
    author: string; authorInitials: string; date: string; tags: string[];
  }[];
};

/* documents.kind is open-ended (page, commit, pr, issue, answer, decision, …);
   the browser draws three. Everything that is not a pull request or a chat
   thread is a page — which is what those kinds are. */
const KIND: Record<string, KnowledgeResult["kind"]> = {
  pr: "pr",
  thread: "thread",
};

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
}`;

type DocRes = {
  document: {
    id: number; source: string; title: string; snippet: string; kind: string;
    author: string; date: string; tags: string[]; watched: boolean;
  } | null;
  revisions: { id: number; actor: string; verb: string; at: string }[];
};

export function mapKnowledgeDoc(res: DocRes | null): KnowledgeDoc | null {
  const d = res?.document;
  if (!d) return null;
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
    // Facts and related documents are both real sections of this rail with no
    // honest source yet: `facts` is a workspace-wide table with no document
    // foreign key, and edges are only exposed as the whole lineage graph, not
    // per document. Empty until the backend can answer for THIS document —
    // another document's facts under this title would be worse than none.
    facts: [],
    related: [],
    timeline: (res?.revisions ?? []).map((r) => ({ at: r.at, actor: r.actor, verb: r.verb })),
    watched: d.watched,
  };
}

export function useKnowledge(query = ""): PageData<KnowledgeData> {
  const q = useQuery<KnowledgeResult[]>(QUERY, { variables: { q: query }, map: mapKnowledge });

  /* Which result the rail is describing is ROUTE state, not component state.
     The browser used to keep it to itself, so the rail said "Nothing selected"
     no matter what you clicked. In `?doc=` it survives a reload, can be
     linked to, and is what the page hands back to the browser to highlight. */
  const [params] = useSearchParams();
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
      results: q.data ?? [],
      doc: docQ.data ?? null,
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
