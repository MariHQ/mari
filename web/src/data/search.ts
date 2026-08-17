/* Global search (⌘K), backed by the API's hybrid search.
 *
 * The library owns the overlay, the shortcut and the debounce; this owns what
 * "search" means here. That is the `search` query — BM25 over the text index
 * plus embedding similarity over document and chunk vectors — so the box in
 * the topbar returns the same ranking the rest of the product uses, rather
 * than a second, weaker search invented for the chrome.
 *
 * Documents are the only scope today. Answers, facts and people are all
 * searchable concepts in the product, but none has a query behind it yet, and
 * a scope chip that always comes back empty is worse than one that is absent.
 */

import { gql } from "../lib/api";
import { cleanSnippet } from "./text";
import type { SearchResultGroup, SearchScope } from "@mari-design/components/navigation/GlobalSearch";

const QUERY = `query GlobalSearch($query: String!, $k: Int!) {
  search(query: $query, k: $k) { id title snippet source date kind }
}`;

type Row = {
  id: number;
  title: string;
  snippet: string;
  source: string;
  date: string;
  kind: string;
};

export const SEARCH_SCOPES: SearchScope[] = [
  { id: "docs", label: "Documents" },
];

/** Relative age, for the right-hand meta column. The API returns ISO dates and
    the client formats them (the same rule the document adapters follow). */
function ago(iso: string): string {
  if (!iso) return "";
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "";
  const days = Math.floor((Date.now() - then) / 86_400_000);
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 30) return `${days}d ago`;
  if (days < 365) return `${Math.floor(days / 30)}mo ago`;
  return `${Math.floor(days / 365)}y ago`;
}

/** Run a global search. Returns grouped results for the overlay.

    Failures resolve to no results rather than throwing: the overlay has
    nowhere to put an error, and `gql` already surfaces API outages through the
    page-level error banner. */
export async function globalSearch(query: string): Promise<SearchResultGroup[]> {
  const data = await gql<{ search: Row[] }>(QUERY, { query, k: 12 });
  const rows = data?.search ?? [];
  if (!rows.length) return [];
  return [{
    scope: SEARCH_SCOPES[0],
    results: rows.map((r) => ({
      id: String(r.id),
      scope: "docs",
      title: r.title,
      subtitle: cleanSnippet(r.snippet, r.title),
      meta: ago(r.date),
      // The document route, so Enter opens what was found — and so the row can
      // be cmd-clicked into a new tab like any other link.
      href: `/knowledge/doc?id=${r.id}`,
    })),
  }];
}
