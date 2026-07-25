/* Lineage actions — the graph's writes.
 *
 * `analyzeImpact` is the one handler that answers rather than just succeeding:
 * the assert drawer renders exactly what the analysis returned, so the result
 * comes back through the handler instead of being read from a second query. */

import type { LineageActions } from "@mari-design/components/pages/LineagePage";
import type { DocHistoryRow, ImpactResult } from "@mari-design/components/features/LineageDataModel";
import { gqlResult } from "../../lib/api";
import { mutate, type ActionContext } from "../actions";

/** Severities the drawer buckets by. Anything else is a "mentions" row rather
 *  than an uncolored chip with no bucket. */
const SEVERITIES = new Set(["update-required", "review", "minor"]);

export function lineageActions({ navigate }: ActionContext): LineageActions {
  return {
    // The graph's nodes ARE documents; the drawers offered "Open document" and
    // linked to "#". The library names the destination, the app follows it.
    openDocument: (docId: number) => navigate(`/knowledge/doc?id=${docId}`),
    // x/y are 0..1 fractions of the canvas, which is exactly what `documents.
    // graph_x`/`graph_y` store and what the lineage query reads back.
    pinNode: async ({ docId, x, y }) => {
      await mutate("mutation($docId: Int!, $x: Float!, $y: Float!) { pinNode(documentId: $docId, x: $x, y: $y) }",
        { docId, x, y });
    },
    unpinNode: async (docId: number) => {
      await mutate("mutation($docId: Int!) { unpinNode(documentId: $docId) }", { docId });
    },
    watchDocument: async (docId: number) => {
      await mutate("mutation($docId: Int!) { toggleWatch(documentId: $docId) }", { docId });
    },
    /* The node drawer's History tab, fetched for the node the drawer is
       actually showing. It is a read rather than a write, but it belongs here
       for the same reason `select` does: the library names what it needs and
       the app decides where that comes from. A failure throws with the
       server's own message — the drawer says history could not be loaded,
       which is not the same claim as "this document has no history". */
    loadDocHistory: async (docId: number): Promise<DocHistoryRow[]> => {
      const r = await gqlResult<{ docHistory: DocHistoryRow[] }>(
        "query($docId: Int!) { docHistory(documentId: $docId) { at actor verb detail } }",
        { docId },
      );
      if (!r.ok) throw new Error(r.error);
      return r.data.docHistory ?? [];
    },
    createReviewTask: async ({ title, assignee }) => {
      await mutate(
        "mutation($title: String!, $kind: String!, $kindLabel: String!, $assignee: String!) { createTask(title: $title, kind: $kind, kindLabel: $kindLabel, assignee: $assignee) }",
        { title, kind: "review", kindLabel: "Review", assignee: assignee || "Daniel H." },
      );
    },
    deriveLinks: async () => {
      await mutate("mutation { deriveLinks }");
    },
    saveView: async ({ name, state }) => {
      await mutate("mutation($name: String!, $state: String!) { saveGraphView(name: $name, state: $state) }",
        { name, state });
    },
    analyzeImpact: async (claim: string): Promise<ImpactResult> => {
      const d = await mutate(
        "mutation($claim: String!) { impactAnalysis(claim: $claim) { claim summary docs { title source severity reason } } }",
        { claim },
      );
      const r = d?.impactAnalysis;
      return {
        claim: r?.claim ?? claim,
        summary: r?.summary ?? "",
        docs: (r?.docs ?? [])
          .filter((doc: { severity: string }) => SEVERITIES.has(doc.severity))
          .map((doc: { title: string; source: string; severity: string; reason: string }) => ({
            title: doc.title, source: doc.source,
            severity: doc.severity as ImpactResult["docs"][number]["severity"],
            reason: doc.reason,
          })),
      };
    },
    // No delete-view handler: the toolbar has no control that removes a saved
    // view, so `deleteGraphView` stays unwired rather than being called from
    // somewhere the user cannot see.
  };
}
