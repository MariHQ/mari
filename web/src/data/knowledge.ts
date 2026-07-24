/* Knowledge browser adapter over the hybrid-search root field. */

import type { KnowledgeData } from "@mari-design/components/pages/KnowledgePage";
import type { KnowledgeResult } from "@mari-design/components/features/KnowledgeBrowser";
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

export function useKnowledge(query = ""): PageData<KnowledgeData> {
  const q = useQuery<KnowledgeResult[]>(QUERY, { variables: { q: query }, map: mapKnowledge });
  return {
    data: {
      results: q.data ?? [],
      // The inspector rail needs a document's facts, related docs, timeline
      // and lineage — four more round trips, only worth making once a row is
      // actually selected. Nothing selected on load.
      doc: null,
    },
    loading: q.loading,
    error: q.error ? (q.errorText ?? "Search is temporarily unavailable.") : null,
  };
}
