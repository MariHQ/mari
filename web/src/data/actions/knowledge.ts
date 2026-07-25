/* Knowledge-browser writes.
 *
 * There is one mutation. The rest of this file is routing: search, paging and
 * selection are reads, but WHERE they live is this app's decision, and all
 * three live in the URL. That is what makes a Knowledge search shareable, what
 * makes it survive a reload — and what makes it reach the server at all, since
 * the adapter reads `?q=` and hands it to the hybrid-search field.
 *
 * Nothing in the schema records a bookmark or a per-result sort, so those
 * controls stay local rather than pointing at an endpoint that does not exist.
 */

import type { KnowledgeActions } from "@mari-design/components/pages/KnowledgePage";
import { PAGE } from "../knowledge";
import { mutate, type ActionContext } from "../actions";

const TOGGLE_WATCH = `mutation ToggleWatch($documentId: Int!) { toggleWatch(documentId: $documentId) }`;

/** The Knowledge URL with one parameter changed and the empty ones dropped, so
 *  a shared link carries the search and nothing else. */
function href(next: Record<string, string | null>): string {
  const params = new URLSearchParams(window.location.search);
  for (const [key, value] of Object.entries(next)) {
    if (value === null || value === "") params.delete(key);
    else params.set(key, value);
  }
  const qs = params.toString();
  return qs ? `/knowledge?${qs}` : "/knowledge";
}

export function knowledgeActions({ navigate, replace }: ActionContext): KnowledgeActions {
  return {
    // Same route the page is already on, so the feed, its filters and the
    // scroll position stay exactly where they were; only `?doc=` changes.
    select: ({ id }) => navigate(href({ doc: id })),

    /* A new search is a new result set: the page count resets to the first
       page, and the document selected out of the OLD results is dropped rather
       than left in the rail describing something the feed no longer lists.
       Replaced, not pushed — a search box would otherwise fill the history
       with one entry per keystroke and make Back unusable. */
    setQuery: ({ query }) => replace(href({ q: query, k: null, doc: null })),

    // "Show more" asks the server for a longer page of the same search. It is
    // in the URL for the same reason the query is: reload it and you get the
    // list you were looking at, not the first 40 rows of it.
    showMore: () => {
      const current = Number(new URLSearchParams(window.location.search).get("k"));
      const k = Number.isInteger(current) && current > PAGE ? current : PAGE;
      replace(href({ k: String(k + PAGE) }));
    },

    // The mutation answers with the state it left the subscription in, so the
    // rail shows the server's word rather than its own optimistic flip.
    toggleWatch: async ({ id }) => {
      const d: { toggleWatch: boolean } = await mutate(TOGGLE_WATCH, { documentId: Number(id) });
      return d.toggleWatch;
    },
  };
}
