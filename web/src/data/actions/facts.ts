/* Facts actions — the write half of the fact ledger.
 *
 * One handler per intent the page offers. Each throws on failure and the
 * control that called it shows the server's own message, so a rejected write
 * is exactly as visible as a failed read. */

import type { FactsActions } from "@mari-design/components/pages/FactsPage";
import { mutate } from "../actions";

export function factsActions(): FactsActions {
  return {
    verifyFact: async (id: number) => {
      await mutate("mutation($id: Int!) { verifyFact(id: $id) }", { id });
    },
    addFact: async ({ claim, source, owner }) => {
      // `owner` defaults server-side, so an unowned claim is still accepted
      // rather than being rejected for a field the form left blank.
      await mutate(
        "mutation($claim: String!, $source: String!, $owner: String!) { addFact(claim: $claim, source: $source, owner: $owner) }",
        { claim, source, owner: owner || "Daniel H." },
      );
    },
    scanFacts: async () => {
      await mutate("mutation { scanFacts }");
    },
    createReviewTask: async (fact) => {
      // `factCheck` is the fact-check *detector* and takes a document, not a
      // claim, so re-verification is a task on the ledger row: the same
      // createTask the Tasks page reads back.
      await mutate(
        "mutation($title: String!, $kind: String!, $kindLabel: String!, $assignee: String!) { createTask(title: $title, kind: $kind, kindLabel: $kindLabel, assignee: $assignee) }",
        {
          title: `Re-verify: ${fact.claim}`,
          kind: "factcheck",
          kindLabel: "Fact check",
          assignee: fact.owner || "Daniel H.",
        },
      );
    },
  };
}
