/* Tasks inbox adapter. The standalone form of the Overview "Today's review"
 * card, over the same `tasks` root field — plus `tasksSummary`, the rollup the
 * server counts off those very rows, so the strip can never disagree with the
 * board underneath it. */

import { useState } from "react";
import type { TasksData, Task, TaskAssignee, TaskStrip } from "@mari-design/components/pages/TasksPage";
import { useQuery } from "../lib/api";
import type { PageData } from "./types";

const QUERY = `{
  tasks { id title assigneeInitials kind kindLabel done due overdue }
  tasksSummary { title tags people statValue statLabel }
  members { id name initials status }
}`;

type Res = {
  tasks: {
    id: number; title: string; assigneeInitials: string; kind: string;
    kindLabel: string; done: boolean; due: string; overdue: boolean;
  }[];
  tasksSummary: {
    title: string; tags: string[]; people: string[];
    statValue: string; statLabel: string;
  } | null;
  members: { id: number; name: string; initials: string; status: string }[];
};

export const EMPTY: TasksData = { tasks: [], draft: "", saving: false, strip: null, assignees: [] };

export function mapTasks(res: Res): Task[] {
  return (res.tasks ?? []).map<Task>((t) => ({
    id: t.id, title: t.title, who: t.assigneeInitials,
    kind: t.kind, kindLabel: t.kindLabel, done: t.done,
    // "" is the server's "no deadline". The row formats this date itself, so
    // an empty string would render as the epoch; absent is the honest value.
    due: t.due || undefined,
    // Derived in SQL against the database's own current_date, so it cannot
    // drift from the due date the same row reports.
    overdue: t.overdue,
    // No `doc` and no `priority`: the `tasks` table records neither, so the
    // row draws no document link and no urgency. Both are optional precisely
    // so a board that does not track them shows nothing instead of a guess.
  }));
}

/** Who a task can be filed to: the workspace's own members. `id` is what the
 *  composer hands back to `create`, and `createTask` files a task by the
 *  assignee's NAME, so the name is the id here. Invited-but-not-joined people
 *  are left out — a task filed to someone who has never signed in has no one
 *  to do it. */
export function mapAssignees(res: Res): TaskAssignee[] {
  return (res.members ?? [])
    .filter((m) => m.status === "active")
    .map<TaskAssignee>((m) => ({ id: m.name, name: m.name, initials: m.initials || undefined }));
}

/** The rollup strip, or null on an empty inbox — there is nothing to
 *  summarise, and the board renders without a headline. */
export function mapStrip(res: Res): TaskStrip | null {
  const s = res.tasksSummary;
  if (!s) return null;
  return {
    title: s.title,
    tags: s.tags ?? [],
    people: s.people ?? [],
    statValue: s.statValue,
    statLabel: s.statLabel,
  };
}

/** Pure: rows + the rollup + local composer state → everything the page
 *  renders. */
export function buildTasks(
  tasks: Task[], strip: TaskStrip | null, draft: string, assignees: TaskAssignee[] = [],
): TasksData {
  return {
    tasks, draft, saving: false, strip, assignees,
    // No `priorities`: this workspace stores no priority vocabulary (the
    // `tasks` table has no column for one), and the composer then draws no
    // priority control rather than one whose value nothing can keep.
  };
}

export function useTasks(): PageData<TasksData> {
  // Composer contents are local UI state, not workspace content.
  const [draft] = useState("");
  const q = useQuery<Res>(QUERY, { map: (d: Res) => d });
  return {
    data: buildTasks(
      q.data ? mapTasks(q.data) : [],
      q.data ? mapStrip(q.data) : null,
      draft,
      q.data ? mapAssignees(q.data) : [],
    ),
    loading: q.loading,
    error: q.error ? (q.errorText ?? "Tasks are temporarily unavailable.") : null,
  };
}
