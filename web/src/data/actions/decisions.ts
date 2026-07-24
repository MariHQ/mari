/* Decision-ledger writes.
 *
 * Not wired, deliberately: the card's "Ignore" control. `supersedeDecision`
 * needs the statement that replaces the one being set aside — and it INSERTS
 * that statement as a new ratified decision. The button collects no such text,
 * so wiring it would file a decision literally named "(pending replacement)".
 * It keeps its local behaviour until the control asks for a replacement.
 */

import type { DecisionsActions } from "@mari-design/components/pages/DecisionsPage";
import { mutate } from "../actions";

const CAPTURE = `mutation AddDecision($statement: String!, $context: String!, $sourceLabel: String!) {
  addDecision(statement: $statement, context: $context, sourceLabel: $sourceLabel)
}`;

const RATIFY = `mutation RatifyDecision($id: Int!) { ratifyDecision(id: $id) }`;

const IMPACT = `mutation DecisionImpact($id: Int!) {
  decisionImpact(id: $id) { summary docs { title source severity reason } }
}`;

const SCAN = `mutation ScanDecisions { scanDecisions }`;

type ImpactRes = {
  decisionImpact: { summary: string; docs: { title: string; source: string; severity: string; reason: string }[] };
};

export function decisionsActions(): DecisionsActions {
  return {
    capture: async ({ statement, context, source }) => {
      // "" is a real value here: the resolver files an uncredited capture as
      // "Captured in Mari" rather than inventing a channel it never read.
      await mutate(CAPTURE, { statement, context, sourceLabel: source });
    },
    ratify: async ({ id }) => { await mutate(RATIFY, { id }); },
    runImpact: async ({ id }) => {
      const d: ImpactRes = await mutate(IMPACT, { id });
      const r = d.decisionImpact;
      return { summary: r.summary, docs: r.docs ?? [] };
    },
    scan: async () => { await mutate(SCAN); },
  };
}
