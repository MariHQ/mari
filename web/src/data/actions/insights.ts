/* Insights actions — the window, the two measurement runs, and the glossary
 * triage. */

import type { InsightsActions } from "@mari-design/components/pages/InsightsPage";
import { mutate, type ActionContext } from "../actions";
import { rangeHref } from "../range";

export function insightsActions({ navigate }: ActionContext): InsightsActions {
  return {
    /* The window lives in the route, so the adapter re-queries `insightStats`
       with the new bounds and the dashboard someone narrowed is a link. */
    setRange: (range) => navigate(rangeHref("/insights", range, new URLSearchParams(window.location.search))),

    /* The readability rows carry the document's own id, so the grade finally
       leads back to the document it is about. Same route the lineage graph and
       the knowledge browser open a document on. */
    openDoc: (id: number) => navigate(`/knowledge/doc?id=${id}`),

    /* No `openFreshness`. The bands are counted in SQL over
       `documents.updated_src`; nothing in the API lists the documents behind
       one band of one source, and the Knowledge browser searches text rather
       than filtering on freshness — so there is no destination to send the
       click to. The chart draws a plain bar rather than a drill-through that
       lands somewhere it did not promise. */

    scoreReadability: async () => {
      await mutate("mutation { scoreReadability }");
    },
    harvestGlossary: async () => {
      await mutate("mutation { harvestGlossary }");
    },
    // Accept and Dismiss are the same write with opposite intent: the server
    // promotes the candidate into the glossary or drops it.
    resolveGlossaryTerm: async ({ id, accept }) => {
      await mutate(
        "mutation($id: Int!, $accept: Boolean!) { promoteGlossaryCandidate(id: $id, accept: $accept) }",
        { id, accept },
      );
    },
  };
}
