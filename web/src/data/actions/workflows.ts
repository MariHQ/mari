/* Workflows writes — both tabs.
 *
 * The Observed tab's filters, its focused workflow, and the tab itself all
 * live in the URL, so every one of them is a `replace` rather than a mutation:
 * they are state that must be shareable, not places to go back to.
 *
 * This replaces src/data/actions/trajectories.ts and .../answers.ts.
 */

import type { WorkflowsActions } from "@mari-design/components/pages/WorkflowsPage";
import type { ActionContext } from "./index";
import { mutate } from "./index";

const SAVE_ANSWER = `mutation UpsertAnswer($id: Int, $question: String!, $answer: String!) {
  upsertAnswer(id: $id, question: $question, answer: $answer)
}`;

const SET_ANSWER_STATUS = `mutation SetAnswerStatus($id: Int!, $status: String!) {
  setAnswerStatus(id: $id, status: $status)
}`;

const SET_ANSWER_CHANNELS = `mutation SetAnswerChannels($id: Int!, $channels: [String!]!) {
  setAnswerChannels(id: $id, channels: $channels)
}`;

const SCAN = `mutation ScanAnswerCandidates($sources: [String!]!) {
  scanAnswerCandidates(sources: $sources) { question draftAnswer sourceLabel confidence }
}`;

const PROMOTE_WORKFLOW = `mutation PromoteToWorkflow($trajectoryId: Int!, $name: String!) {
  promoteTrajectoryToWorkflow(trajectoryId: $trajectoryId, name: $name) {
    id name status nodeCount
  }
}`;

const PROMOTE_ANSWER = `mutation PromoteToAnswer($trajectoryId: Int!) {
  promoteTrajectoryToAnswer(trajectoryId: $trajectoryId)
}`;

const REJECT = `mutation RejectTrajectory($trajectoryId: Int!, $rejected: Boolean!) {
  rejectTrajectory(trajectoryId: $trajectoryId, rejected: $rejected)
}`;

const DELETE = `mutation DeleteTrajectory($trajectoryId: Int!) {
  deleteTrajectory(trajectoryId: $trajectoryId)
}`;

/** The API grades a candidate high/medium/low; the wizard shows a percentage
    and auto-accepts at 75. These are the midpoints of those three bands, not a
    measurement — the model does not produce one, and inventing more precision
    than exists would be the lie the number is there to avoid. */
const CONFIDENCE: Record<string, number> = { high: 90, medium: 60, low: 30 };

/** Every piece of view state this page keeps in the URL. */
type Route = {
  tab?: "observed" | "answers";
  category?: string | null;
  status?: string | null;
  failures?: string | null;
  q?: string | null;
  offset?: number;
  trajectory?: number | null;
};

export function workflowsActions({ replace }: ActionContext): WorkflowsActions {
  /* Read from the live URL rather than from a captured copy: these handlers
     are memoised for the life of the route, and a filter set from a captured
     snapshot would silently drop whatever the reader changed in between. */
  const route = (changes: Route) => {
    const params = new URLSearchParams(window.location.search);
    const set = (key: string, value: string | null | undefined) => {
      if (value) params.set(key, value);
      else params.delete(key);
    };
    if ("tab" in changes) set("tab", changes.tab === "observed" ? null : changes.tab);
    // Every filter change resets paging: page 4 of the old filter is not page 4
    // of the new one, and landing on an empty page reads as "no results".
    for (const key of ["category", "status", "failures", "q"] as const) {
      if (key in changes) { set(key, changes[key]); params.delete("offset"); }
    }
    if (changes.offset !== undefined) {
      set("offset", changes.offset > 0 ? String(changes.offset) : null);
    }
    if ("trajectory" in changes) {
      set("trajectory", changes.trajectory ? String(changes.trajectory) : null);
    }
    const query = params.toString();
    replace(query ? `/workflows?${query}` : "/workflows");
  };

  return {
    /* ── view state ── */
    setTab: (tab) => route({ tab, trajectory: tab === "observed" ? undefined : null }),
    setCategory: (category) => route({ category }),
    setStatusFilter: (status) => route({ status }),
    setFailures: (failures) => route({ failures }),
    setSearch: (search) => route({ q: search.trim() || null }),
    setOffset: (offset) => route({ offset }),
    openTrajectory: (trajectoryId) => route({ trajectory: trajectoryId }),
    // The Approved answers card's "open the workflow this came from": switch
    // tab and focus the run in one entry, so Back returns to the answer.
    openWorkflow: (trajectoryId) => route({ tab: "observed", trajectory: trajectoryId }),

    /* ── observed writes ── */
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
    /* Answers with the workflow the server actually saved, not with an echo of
       what was asked for. The node count is the part worth showing: an
       excluded tool is not in the workflow, so the count is how tuning becomes
       visible on the card. */
    promote: async (trajectoryId, name) => {
      const data = await mutate(PROMOTE_WORKFLOW, { trajectoryId, name });
      const made = data.promoteTrajectoryToWorkflow;
      return {
        id: Number(made.id),
        name: String(made.name),
        status: String(made.status),
        nodeCount: Number(made.nodeCount),
      };
    },
    promoteToAnswer: async (trajectoryId) => { await mutate(PROMOTE_ANSWER, { trajectoryId }); },
    // Restoring is the same write with the flag off: rejection is a judgement,
    // not a delete, so the control that made it is the control that undoes it.
    reject: async (trajectoryId, rejected) => { await mutate(REJECT, { trajectoryId, rejected }); },
    remove: async (trajectoryId) => { await mutate(DELETE, { trajectoryId }); },

    /* ── codified workflow lifecycle ── */
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
      const data = await mutate(`mutation { reconcileStaleAssistantWorkflows(limit: 50) }`);
      return Number(data.reconcileStaleAssistantWorkflows);
    },
    deleteWorkflow: async (workflowId) => {
      await mutate(`mutation($workflowId: Int!) { deleteAssistantWorkflow(workflowId: $workflowId) }`, { workflowId });
    },

    /* ── clusters and harvesting ── */
    suggestSplitName: async (trajectoryId) => {
      const data = await mutate(`mutation($trajectoryId: Int!) { suggestWorkflowSplitName(trajectoryId: $trajectoryId) }`, { trajectoryId });
      return String(data.suggestWorkflowSplitName);
    },
    splitWorkflow: async (trajectoryId, name) => {
      const data = await mutate(`mutation($trajectoryId: Int!, $name: String!) {
        splitAssistantWorkflow(trajectoryId: $trajectoryId, name: $name)
      }`, { trajectoryId, name });
      return Number(data.splitAssistantWorkflow);
    },
    harvestCandidates: async () => {
      const data = await mutate(`mutation { harvestWorkflowCandidates(limit: 100) }`);
      return Array.isArray(data.harvestWorkflowCandidates) ? data.harvestWorkflowCandidates : [];
    },
    codifyCandidate: async (candidate) => {
      if (candidate.existingWorkflowId) {
        const data = await mutate(`mutation($trajectoryId: Int!, $name: String!) {
          splitAssistantWorkflow(trajectoryId: $trajectoryId, name: $name)
        }`, { trajectoryId: candidate.seedTrajectoryId, name: candidate.name });
        return Number(data.splitAssistantWorkflow);
      }
      const data = await mutate(PROMOTE_WORKFLOW, { trajectoryId: candidate.seedTrajectoryId, name: candidate.name });
      return Number(data.promoteTrajectoryToWorkflow.id);
    },

    /* ── answers writes ── */
    save: async ({ id, question, answer }) => { await mutate(SAVE_ANSWER, { id, question, answer }); },
    // No id: the same mutation, inserting rather than updating. It lands as a
    // draft, which is why the composer says so instead of claiming an approval.
    create: async ({ question, answer }) => { await mutate(SAVE_ANSWER, { question, answer }); },
    setStatus: async ({ id, status }) => { await mutate(SET_ANSWER_STATUS, { id, status }); },
    setChannels: async ({ id, channels }) => { await mutate(SET_ANSWER_CHANNELS, { id, channels }); },

    harvest: async (sources) => {
      const data = await mutate(SCAN, { sources });
      const rows: { question: string; draftAnswer: string; sourceLabel: string; confidence: string }[] =
        data?.scanAnswerCandidates ?? [];
      return rows.map((r) => ({
        question: r.question,
        draft: r.draftAnswer,
        source: r.sourceLabel,
        confidence: CONFIDENCE[r.confidence] ?? 60,
      }));
    },

    /* Saved one at a time rather than in a batch: there is no bulk mutation,
       and a partial failure has to leave the answers that DID save in place.
       The wizard reports the whole thing as failed if any of them throws,
       which is honest — some were saved and the list will show them. */
    importAnswers: async (drafts) => {
      for (const d of drafts) await mutate(SAVE_ANSWER, { question: d.question, answer: d.answer });
    },
  };
}
