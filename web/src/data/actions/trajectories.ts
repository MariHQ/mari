import type { TrajectoriesActions } from "@mari-design/components/pages/TrajectoriesPage";
import type { ActionContext } from "./index";
import { mutate } from "./index";

export function trajectoriesActions({ replace }: ActionContext): TrajectoriesActions {
  const route = (changes: { category?: string | null; offset?: number }) => {
    const params = new URLSearchParams(window.location.search);
    if ("category" in changes) {
      if (changes.category) params.set("category", changes.category);
      else params.delete("category");
      params.delete("offset");
    }
    if (changes.offset !== undefined && changes.offset > 0) params.set("offset", String(changes.offset));
    else if (changes.offset !== undefined) params.delete("offset");
    const query = params.toString();
    replace(query ? `/workflows?${query}` : "/workflows");
  };
  return {
    setCategory: (category) => route({ category }),
    setOffset: (offset) => route({ offset }),
    tuneStep: async (trajectoryId, ordinal, disposition, editedArgs) => {
      await mutate(`mutation($trajectoryId: Int!, $ordinal: Int!, $disposition: String!, $editedArgs: JSON) {
        tuneTrajectoryStep(trajectoryId: $trajectoryId, ordinal: $ordinal, disposition: $disposition, editedArgs: $editedArgs)
      }`, { trajectoryId, ordinal, disposition, editedArgs });
    },
    tuneEvidence: async (trajectoryId, documentId, relevance, note) => {
      await mutate(`mutation($trajectoryId: Int!, $documentId: Int!, $relevance: String!, $note: String!) {
        tuneTrajectoryEvidence(trajectoryId: $trajectoryId, documentId: $documentId, relevance: $relevance, note: $note)
      }`, { trajectoryId, documentId, relevance, note });
    },
    promote: async (trajectoryId, name) => {
      const data = await mutate(`mutation($trajectoryId: Int!, $name: String!) {
        promoteTrajectoryToWorkflow(trajectoryId: $trajectoryId, name: $name)
      }`, { trajectoryId, name });
      return Number(data.promoteTrajectoryToWorkflow);
    },
    setWorkflowEnabled: async (workflowId, enabled) => {
      await mutate(`mutation($workflowId: Int!, $enabled: Boolean!) {
        setAssistantWorkflowEnabled(workflowId: $workflowId, enabled: $enabled)
      }`, { workflowId, enabled });
    },
    setWorkflowCache: async (workflowId, enabled) => {
      await mutate(`mutation($workflowId: Int!, $enabled: Boolean!) {
        setAssistantWorkflowCache(workflowId: $workflowId, enabled: $enabled)
      }`, { workflowId, enabled });
    },
    reconcileStale: async () => {
      const data = await mutate(`mutation {
        reconcileStaleAssistantWorkflows(limit: 50)
      }`);
      return Number(data.reconcileStaleAssistantWorkflows);
    },
    deleteWorkflow: async (workflowId) => {
      await mutate(`mutation($workflowId: Int!) {
        deleteAssistantWorkflow(workflowId: $workflowId)
      }`, { workflowId });
    },
  };
}
