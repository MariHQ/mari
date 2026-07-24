/* Flows actions — start, pause, create, retrigger and delete automations. */

import type { FlowsActions } from "@mari-design/components/pages/FlowsPage";
import { mutate } from "../actions";

export function flowsActions(): FlowsActions {
  return {
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
    createFlow: async ({ name, description, steps, color }) => {
      // The draft lands paused, so nothing fires until it is turned on. The
      // engine reads `nodes` as the step list, which is what `steps` carries.
      const id = await mutate(
        `mutation($name: String!, $description: String!, $steps: JSON!, $color: String!, $pinned: Boolean!) {
           saveWorkflow(name: $name, description: $description, steps: $steps, color: $color, pinned: $pinned)
         }`,
        { name, description, steps, color, pinned: false },
      );
      const newId = id?.saveWorkflow;
      if (typeof newId === "number" && newId > 0) {
        await mutate("mutation($id: Int!, $status: String!) { setWorkflowStatus(id: $id, status: $status) }",
          { id: newId, status: "paused" });
      }
    },
    saveTrigger: async ({ id, trigger }) => {
      // The server takes the trigger as a JSON string and validates the shape,
      // rejecting an unknown key rather than silently dropping it.
      const on = trigger.on ?? "";
      const payload = on === "schedule"
        ? { on, every_minutes: trigger.every_minutes ?? 1440 }
        : on === "document_added" || on === "document_changed"
          ? {
              on,
              source_id: trigger.source_id ?? null,
              tag: trigger.tag ?? null,
              path_glob: trigger.path_glob ?? null,
            }
          : { on: "" };
      await mutate(
        "mutation($id: Int!, $trigger: String!) { setWorkflowTrigger(workflowId: $id, trigger: $trigger) }",
        { id, trigger: JSON.stringify(payload) },
      );
    },
    approveRun: async (runId: string) => {
      await mutate("mutation($runId: Int!) { approveRun(runId: $runId) }", { runId: Number(runId) });
    },
    // No re-run handler: `runWorkflow` needs the workflow, and a run row does
    // not carry which workflow it belongs to, so the inspector's Re-run stays
    // the local echo rather than guessing an id.
  };
}
