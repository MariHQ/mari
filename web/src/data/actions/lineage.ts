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

export function lineageActions({ navigate, replace, currentUserName }: ActionContext): LineageActions {
  /* Moving the focus or the question is REPLACE, not push.
   *
   * Both used to push, so walking a lineage — the whole point of the page —
   * filled the history stack one node at a time and Back crawled the graph
   * backwards instead of leaving it. They still belong in the URL: the view
   * has to stay shareable and reloadable, and the breadcrumb reads its way
   * back out of them. They are just not places to go back to. Opening a
   * document is, and that one pushes. */
  const view = (patch: Record<string, string | null>) => {
    const params = new URLSearchParams(window.location.search);
    for (const [key, value] of Object.entries(patch)) {
      if (value === null) params.delete(key); else params.set(key, value);
    }
    const qs = params.toString();
    replace(qs ? `/lineage?${qs}` : "/lineage");
  };

  return {
    // The graph's nodes ARE documents; the drawers offered "Open document" and
    // linked to "#". The library names the destination, the app follows it.
    // A real destination, so a real history entry.
    openDocument: (docId: number) => navigate(`/knowledge/doc?id=${docId}`),
    setFocalNode: (nodeId: string) => view({ focal: nodeId }),
    setMode: (mode, focalId) => view({ mode, ...(focalId ? { focal: focalId } : null) }),
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
        { title, kind: "review", kindLabel: "Review", assignee: assignee || currentUserName },
      );
    },
    deriveLinks: async () => {
      await mutate("mutation { deriveLinks }");
    },
    /* The id comes back so the toolbar can offer to remove the view it just
       saved, without waiting for the next read to hand it one. */
    saveView: async ({ name, state }): Promise<number | void> => {
      const d = await mutate("mutation($name: String!, $state: String!) { saveGraphView(name: $name, state: $state) }",
        { name, state });
      const id = d?.saveGraphView;
      return typeof id === "number" ? id : undefined;
    },
    deleteView: async ({ id }) => {
      await mutate("mutation($id: Int!) { deleteGraphView(id: $id) }", { id });
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
  };
}
