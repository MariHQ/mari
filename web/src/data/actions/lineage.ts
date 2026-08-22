/* Lineage actions — the graph's writes.
 *
 * `analyzeImpact` is the one handler that answers rather than just succeeding:
 * the assert drawer renders exactly what the analysis returned, so the result
 * comes back through the handler instead of being read from a second query. */

import type { LineageActions } from "@mari-design/components/pages/LineagePage";
import type { DocHistoryRow, ImpactResult } from "@mari-design/components/features/LineageDataModel";
import { SEVERITY_TASK } from "@mari-design/components/features/LineageDataModel";
import { gqlResult } from "../../lib/api";
import { mutate, type ActionContext } from "../actions";

/** Severities the drawer buckets by. Anything else is a "mentions" row rather
 *  than an uncolored chip with no bucket. */
const SEVERITIES = new Set(["update-required", "review", "minor"]);

/** One impacted document as the mutation returns it. */
type ImpactRow = { title: string; source: string; severity: string; reason: string; documentId: number };

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
        "mutation($claim: String!) { impactAnalysis(claim: $claim) { claim summary docs { title source severity reason documentId } } }",
        { claim },
      );
      const r = d?.impactAnalysis;
      return {
        claim: r?.claim ?? claim,
        summary: r?.summary ?? "",
        docs: (r?.docs ?? [])
          .filter((doc: { severity: string }) => SEVERITIES.has(doc.severity))
          .map((doc: ImpactRow) => ({
            title: doc.title, source: doc.source,
            severity: doc.severity as ImpactResult["docs"][number]["severity"],
            reason: doc.reason,
            // The id is what lets the page light the right card on the graph
            // and open a task against the right document. 0 is the server
            // saying it has no id for this row, which is not a document.
            docId: doc.documentId || undefined,
          })),
      };
    },
    /* One task per impacted document, from the assert drawer's bulk create.
       There is no bulk mutation, so this is `createTask` per document; the
       count that comes back is what the drawer reports, so a partial run
       reports what it actually opened rather than what it set out to.

       The severity decides the KIND: an update-required document is a stale
       document to fix, a review is a fact check, a mention is an approval to
       glance at. `createReviewTask` keeps its own semantics — one task, one
       document, from the node drawer. */
    createImpactTasks: async (docs) => {
      let created = 0;
      for (const doc of docs) {
        const task = SEVERITY_TASK[doc.severity];
        await mutate(
          "mutation($title: String!, $kind: String!, $kindLabel: String!, $assignee: String!, $subjectType: String!, $subjectId: String!, $subjectTitle: String!, $subjectHref: String!) { createTask(title: $title, kind: $kind, kindLabel: $kindLabel, assignee: $assignee, subjectType: $subjectType, subjectId: $subjectId, subjectTitle: $subjectTitle, subjectHref: $subjectHref) }",
          {
            title: `${doc.title}: ${doc.reason}`,
            kind: task.kind,
            kindLabel: task.kindLabel,
            // Unassigned: nobody chose an owner for these, and the queue says
            // so rather than putting whoever ran the analysis on all of them.
            assignee: "",
            subjectType: doc.docId ? "document" : "",
            subjectId: doc.docId ? String(doc.docId) : "",
            subjectTitle: doc.docId ? doc.title : "",
            subjectHref: doc.docId ? `/knowledge/doc?id=${doc.docId}` : "",
          },
        );
        created += 1;
      }
      return created;
    },
  };
}
