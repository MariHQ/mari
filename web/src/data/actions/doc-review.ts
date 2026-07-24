/* Doc Review writes.
 *
 * Every mutation here is per-document, and the document is the one in the URL —
 * `/knowledge/doc?id=N`, read the same way `src/data/doc-review.ts` reads it.
 * The page cannot pass the id down: its `data` carries the document's title and
 * body, not its key.
 *
 * Not wired: "Share". Nothing in the schema shares a document, so the control
 * keeps whatever local behaviour it has rather than pointing at a 404.
 */

import type { DocReviewActions } from "@mari-design/components/pages/DocReviewPage";
import { mutate } from "../actions";

/** The document under review, or 0 — which matches no row, so a mutation fired
 *  from a route with no `?id=` fails loudly instead of editing something else. */
function docId(): number {
  const id = Number(new URLSearchParams(window.location.search).get("id"));
  return Number.isInteger(id) && id > 0 ? id : 0;
}

const SAVE = `mutation UpdateDocument($id: Int!, $body: String!) {
  updateDocument(id: $id, body: $body)
}`;

const SET_CHANGE = `mutation SetChangeStatus($id: Int!, $status: String!) {
  setChangeStatus(id: $id, status: $status)
}`;

const ACCEPT_ALL = `mutation AcceptAllChanges($documentId: Int!) {
  acceptAllChanges(documentId: $documentId)
}`;

const REFINE = `mutation RunRefinement($documentId: Int!, $skill: String!) {
  runRefinement(documentId: $documentId, skill: $skill)
}`;

const FACT_CHECK = `mutation FactCheck($documentId: Int!) { factCheck(documentId: $documentId) }`;

const TOGGLE_WATCH = `mutation ToggleWatch($documentId: Int!) { toggleWatch(documentId: $documentId) }`;

const CREATE_TASK = `mutation CreateTask($title: String!, $assignee: String!, $due: String) {
  createTask(title: $title, kind: "factcheck", kindLabel: "Fact check", assignee: $assignee, due: $due)
}`;

export function docReviewActions(): DocReviewActions {
  return {
    save: async ({ body }) => { await mutate(SAVE, { id: docId(), body }); },

    acceptChange: async ({ id }) => { await mutate(SET_CHANGE, { id, status: "accepted" }); },
    rejectChange: async ({ id }) => { await mutate(SET_CHANGE, { id, status: "rejected" }); },
    acceptAll: async () => { await mutate(ACCEPT_ALL, { documentId: docId() }); },

    // Both of these answer with a count, and both panels report it: a run that
    // proposed nothing has to say so, or it reads as a run that failed.
    run: async ({ skill }) => {
      const d: { runRefinement: number } = await mutate(REFINE, { documentId: docId(), skill });
      return d.runRefinement;
    },
    runFactCheck: async () => {
      const d: { factCheck: number } = await mutate(FACT_CHECK, { documentId: docId() });
      return d.factCheck;
    },

    toggleWatch: async () => {
      const d: { toggleWatch: boolean } = await mutate(TOGGLE_WATCH, { documentId: docId() });
      return d.toggleWatch;
    },

    // The task the fact-check panel opens is about THIS document, so its title
    // names it. `due` is the ISO date the picker carries, never its label.
    createReviewTask: async ({ assignee, due }) => {
      const title = `Review fact check on document #${docId()}`;
      await mutate(CREATE_TASK, { title, assignee, due });
    },
  };
}
