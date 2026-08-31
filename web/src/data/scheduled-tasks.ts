import type { ScheduledTask, ScheduledTasksData } from "@mari-design/components/pages/ScheduledTasksPage";
import { useQuery } from "../lib/api";
import type { PageData } from "./types";

const QUERY = `query ScheduledTasks {
  workflows {
    id name description status trigger scheduleCapable
    lastRunNumber lastRunStatus lastRunStarted
  }
}`;

type WorkflowRes = {
  id: number; name: string; description: string; status: string;
  trigger: { on?: string; every_minutes?: number } | null;
  scheduleCapable: boolean; lastRunNumber: number | null;
  lastRunStatus: string; lastRunStarted: string;
};

export function useScheduledTasks(): PageData<ScheduledTasksData> {
  const query = useQuery<{ workflows: WorkflowRes[] }>(QUERY);
  const tasks = (query.data?.workflows ?? [])
    .filter((workflow) => workflow.scheduleCapable)
    .map<ScheduledTask>((workflow) => ({
      id: workflow.id, name: workflow.name, description: workflow.description,
      status: workflow.status,
      scheduleMinutes: workflow.trigger?.on === "schedule"
        ? Number(workflow.trigger.every_minutes) : null,
      lastRunNumber: workflow.lastRunNumber,
      lastRunStatus: workflow.lastRunStatus,
      lastRunStarted: workflow.lastRunStarted,
    }));
  return {
    data: { tasks }, loading: query.loading,
    error: query.error ? (query.errorText ?? "Scheduled tasks are temporarily unavailable.") : null,
  };
}
