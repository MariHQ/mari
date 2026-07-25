/* Flows actions — create, edit, start, pause, retrigger and delete automations.
 *
 * Creating a flow is three writes, not one: the workflow row (with the steps it
 * starts from), its trigger, and the paused status that stops it firing before
 * anyone has looked at it. They are here rather than in the library because
 * only this half knows the transport — and because the last thing a create
 * does is ROUTE: a flow with no pipeline is not finished, so the create lands
 * in the pipeline editor rather than back on the list. */

import type { FlowsActions } from "@mari-design/components/pages/FlowsPage";
import { gql } from "../../lib/api";
import { mutate, type ActionContext } from "./index";

const CREATE = `mutation($name: String!, $description: String!, $steps: JSON!, $color: String!) {
  saveWorkflow(name: $name, description: $description, steps: $steps, color: $color, pinned: false)
}`;
/* The update path takes no colour or pin: `saveWorkflow` ignores both when an
   id is given, and sending a value the server drops reads as an edit that did
   not stick. */
const UPDATE = `mutation($id: Int!, $name: String!, $description: String!, $steps: JSON!) {
  saveWorkflow(id: $id, name: $name, description: $description, steps: $steps)
}`;
const SET_STATUS = `mutation($id: Int!, $status: String!) { setWorkflowStatus(id: $id, status: $status) }`;
const SET_TRIGGER = `mutation($id: Int!, $trigger: String!) { setWorkflowTrigger(workflowId: $id, trigger: $trigger) }`;
const NODES = `{ workflows { id nodes } }`;

/** The trigger, narrowed to the shape `setWorkflowTrigger` validates. It
 *  rejects an unknown key rather than silently dropping it, so the payload is
 *  built by event rather than passed through. */
function triggerPayload(t: { on?: string | null; every_minutes?: number | null; source_id?: number | null; tag?: string | null; path_glob?: string | null }) {
  const on = t.on ?? "";
  if (on === "schedule") return { on, every_minutes: t.every_minutes ?? 1440 };
  if (on === "document_added" || on === "document_changed") {
    return { on, source_id: t.source_id ?? null, tag: t.tag ?? null, path_glob: t.path_glob ?? null };
  }
  return { on: "" };
}

/** The pipeline a new flow starts with: the template's real steps, or the one
 *  step every flow has — its source event, labelled with the trigger that was
 *  just chosen. A blank `nodes` would give the editor nothing to open on. */
async function startingSteps(fromId: number | null, whenLabel: string): Promise<unknown[]> {
  if (fromId != null) {
    const res = await gql<{ workflows: { id: number; nodes: unknown[] | null }[] }>(NODES);
    const src = (res?.workflows ?? []).find((w) => w.id === fromId);
    if (src?.nodes?.length) return src.nodes;
  }
  return [{ kind: "trigger", label: whenLabel, config: { label: whenLabel, query: "" } }];
}

/** What the flow's first step is called, read off the trigger it was given. */
function whenLabelFor(t: { on?: string | null; every_minutes?: number | null }): string {
  const on = t.on ?? "";
  if (on === "schedule") return `Every ${t.every_minutes ?? 1440} minutes`;
  if (on === "document_added") return "Document added";
  if (on === "document_changed") return "Document changed";
  return "Run manually";
}

export function flowsActions({ navigate }: ActionContext): FlowsActions {
  return {
    openFlow: (id: number) => navigate(`/flows?flow=${id}`),
    openFlows: () => navigate("/flows"),
    runFlow: async ({ id, dryRun }) => {
      // Returns the new run number; the list re-reads and the run marks move.
      await mutate("mutation($id: Int!, $dryRun: Boolean!) { runWorkflow(workflowId: $id, dryRun: $dryRun) }",
        { id, dryRun });
    },
    setFlowEnabled: async ({ id, enabled }) => {
      await mutate("mutation($id: Int!, $status: String!) { setWorkflowStatus(id: $id, status: $status) }",
        { id, status: enabled ? "active" : "paused" });
    },
    deleteFlow: async (id: number) => {
      await mutate("mutation($id: Int!) { deleteWorkflow(id: $id) }", { id });
    },
    createFlow: async ({ name, description, color, trigger, fromId }) => {
      const steps = await startingSteps(fromId, whenLabelFor(trigger));
      const d = await mutate(CREATE, { name, description, steps, color });
      const newId = d?.saveWorkflow;
      if (typeof newId !== "number" || newId <= 0) {
        throw new Error(`"${name}" could not be created. A flow with that name may already exist.`);
      }
      await mutate(SET_TRIGGER, { id: newId, trigger: JSON.stringify(triggerPayload(trigger)) });
      // Paused: a flow nobody has given a pipeline must not start firing.
      await mutate(SET_STATUS, { id: newId, status: "paused" });
      // The pipeline is the rest of the job, so the create hands over to the
      // surface that does it.
      navigate(`/flows?flow=${newId}`);
    },
    saveFlow: async ({ id, name, description, enabled, steps }) => {
      // `steps` IS the engine's node list: kind, label and config per step, so
      // what the editor drew is what the next run executes.
      await mutate(UPDATE, { id, name, description, steps });
      await mutate(SET_STATUS, { id, status: enabled ? "active" : "paused" });
    },
    saveTrigger: async ({ id, trigger }) => {
      await mutate(SET_TRIGGER, { id, trigger: JSON.stringify(triggerPayload(trigger)) });
    },
    approveRun: async (runId: string) => {
      await mutate("mutation($runId: Int!) { approveRun(runId: $runId) }", { runId: Number(runId) });
    },
    // No re-run handler: `runWorkflow` needs the workflow, and a run row does
    // not carry which workflow it belongs to, so the inspector's Re-run stays
    // the local echo rather than guessing an id.
  };
}
