/* Knowledge-browser writes.
 *
 * There is one mutation. Search, the facet rail, sort and the result bookmarks
 * are reads or local view state, and nothing in the schema records a bookmark
 * or a per-document tag, so those controls stay local rather than pointing at
 * an endpoint that does not exist.
 *
 * Selecting a result is not a write at all, but it is still this app's job:
 * the page reports which result was picked and the app decides where that
 * lives. Here it lives in the URL, so the rail survives a reload and a
 * particular document can be linked to.
 */

import type { KnowledgeActions } from "@mari-design/components/pages/KnowledgePage";
import { mutate, type ActionContext } from "../actions";

const TOGGLE_WATCH = `mutation ToggleWatch($documentId: Int!) { toggleWatch(documentId: $documentId) }`;

export function knowledgeActions({ navigate }: ActionContext): KnowledgeActions {
  return {
    // Same route the page is already on, so the feed, its filters and the
    // scroll position stay exactly where they were; only `?doc=` changes.
    select: ({ id }) => navigate(`/knowledge?doc=${encodeURIComponent(id)}`),

    // The mutation answers with the state it left the subscription in, so the
    // rail shows the server's word rather than its own optimistic flip.
    toggleWatch: async ({ id }) => {
      const d: { toggleWatch: boolean } = await mutate(TOGGLE_WATCH, { documentId: Number(id) });
      return d.toggleWatch;
    },
  };
}
