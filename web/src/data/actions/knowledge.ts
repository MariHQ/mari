/* Knowledge-browser writes.
 *
 * There is exactly one. Search, the facet rail, sort and the result bookmarks
 * are reads or local view state, and nothing in the schema records a bookmark
 * or a per-document tag, so those controls stay local rather than pointing at
 * an endpoint that does not exist.
 */

import type { KnowledgeActions } from "@mari-design/components/pages/KnowledgePage";
import { mutate } from "../actions";

const TOGGLE_WATCH = `mutation ToggleWatch($documentId: Int!) { toggleWatch(documentId: $documentId) }`;

export function knowledgeActions(): KnowledgeActions {
  return {
    // The mutation answers with the state it left the subscription in, so the
    // rail shows the server's word rather than its own optimistic flip.
    toggleWatch: async ({ id }) => {
      const d: { toggleWatch: boolean } = await mutate(TOGGLE_WATCH, { documentId: Number(id) });
      return d.toggleWatch;
    },
  };
}
