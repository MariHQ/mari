import type { ScheduledTasksActions } from "@mari-design/components/pages/ScheduledTasksPage";
import { mutate } from "./index";

export function scheduledTasksActions(): ScheduledTasksActions {
  return {
    setStatus: async (taskId, status) => {
      await mutate(`mutation($taskId: Int!, $status: String!) {
        setWorkflowStatus(id: $taskId, status: $status)
      }`, { taskId, status });
    },
    setSchedule: async (taskId, everyMinutes) => {
      const trigger = everyMinutes === null ? { on: "" } : { on: "schedule", every_minutes: everyMinutes };
      await mutate(`mutation($taskId: Int!, $trigger: String!) {
        setWorkflowTrigger(workflowId: $taskId, trigger: $trigger)
      }`, { taskId, trigger: JSON.stringify(trigger) });
    },
    runNow: async (taskId) => {
      const data = await mutate(`mutation($taskId: Int!) { runWorkflow(workflowId: $taskId) }`, { taskId });
      return Number(data.runWorkflow);
    },
  };
}
