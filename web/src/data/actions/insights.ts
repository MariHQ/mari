/* Insights actions — the two measurement runs and the glossary triage. */

import type { InsightsActions } from "@mari-design/components/pages/InsightsPage";
import { mutate } from "../actions";

export function insightsActions(): InsightsActions {
  return {
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
