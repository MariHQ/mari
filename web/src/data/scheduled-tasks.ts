import { useEffect } from "react";
import type { ScheduledTask, ScheduledTasksData } from "@mari-design/components/pages/ScheduledTasksPage";
import { useQuery } from "../lib/api";
import type { PageData } from "./types";

const QUERY = `query ScheduledTasks {
  workflows {
    id name description status trigger scheduleCapable nodes
    lastRunNumber lastRunStatus lastRunStarted
  }
}`;

type WorkflowRes = {
  id: number; name: string; description: string; status: string;
  trigger: { on?: string; every_minutes?: number } | null;
  scheduleCapable: boolean; nodes: { kind?: string }[] | null;
  lastRunNumber: number | null;
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
      // Sync rows get the Sources page's sub-hourly cadence options.
      sync: (workflow.nodes ?? []).some((node) => node.kind === "sync_source"),
    }));
  /* A run finishes server-side with no mutation to invalidate anything, so
     the row used to read "running" forever with Run now disabled — the page
     only converged when some other write happened to refetch it. Poll while
     a run is in flight; go quiet the moment nothing is running. */
  const running = tasks.some((task) => task.lastRunStatus === "running");
  const { refetch } = query;
  useEffect(() => {
    if (!running) return;
    const tick = window.setInterval(refetch, 2000);
    return () => window.clearInterval(tick);
  }, [running, refetch]);

  return {
    data: { tasks }, loading: query.loading,
    error: query.error ? (query.errorText ?? "Scheduled tasks are temporarily unavailable.") : null,
  };
}
