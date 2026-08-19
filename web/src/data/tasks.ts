/* Tasks inbox adapter. The standalone form of the Overview "Today's review"
 * card, over the same `tasks` root field — plus `tasksSummary`, the rollup the
 * server counts off those very rows, so the strip can never disagree with the
 * board underneath it. */

import { useMemo, useState } from "react";
import type { TasksData, Task, TaskAssignee, TaskStrip } from "@mari-design/components/pages/TasksPage";
import { useQuery } from "../lib/api";
import type { PageData } from "./types";

const QUERY = `{
  reviewItems(first: 100) {
    items {
      id kind title status source assignee due
      subjectType subjectId subjectTitle subjectHref
      confidence evidenceCount trustedSource
    }
    totalCount pageInfo { endCursor hasNextPage }
  }
  tasksSummary { title tags people statValue statLabel }
  members { id name initials status }
}`;

type Res = {
  reviewItems?: { items: {
    id: string; title: string; assignee: string; kind: string;
    status: string; source: string; due: string;
    subjectType?: string | null; subjectId?: string | null;
    subjectTitle?: string | null; subjectHref?: string | null;
  }[]; totalCount: number; pageInfo: { endCursor: string; hasNextPage: boolean } };
  tasksSummary: {
    title: string; tags: string[]; people: string[];
    statValue: string; statLabel: string;
  } | null;
  members: { id: number; name: string; initials: string; status: string }[];
};

export const EMPTY: TasksData = { tasks: [], draft: "", saving: false, strip: null, assignees: [] };

/** Subject links are data, so integrations can eventually supply them. Keep
 * queue navigation inside this console; an external or scheme URL is context,
 * not an executable link from a review row. */
function internalHref(value: string | null | undefined): string | undefined {
  return value?.startsWith("/") && !value.startsWith("//") ? value : undefined;
}

export function mapTasks(res: Res): Task[] {
  return (res.reviewItems?.items ?? []).map((t) => {
    const subject = t.subjectType && t.subjectId && t.subjectTitle
      ? { type: t.subjectType, id: t.subjectId, title: t.subjectTitle, href: internalHref(t.subjectHref) }
      : undefined;
    return {
      id: t.id.startsWith("task:") ? Number(t.id.slice(5)) : t.id,
      reviewId: t.id.startsWith("task:") ? undefined : t.id,
      title: t.title,
      who: t.assignee ? t.assignee.split(/\s+/).slice(0, 2).map((x) => x[0]?.toUpperCase()).join("") : "?",
      kind: t.kind, kindLabel: ({ fact: "Fact", decision: "Decision", answer: "Answer",
        finding: "Finding", change: "Change", workflow: "Approval", task: "Task" } as Record<string, string>)[t.kind] ?? t.kind,
      done: ["done", "approved", "accepted", "ratified", "verified"].includes(t.status),
      status: t.status, source: t.source, assignee: t.assignee,
      // "" is the server's "no deadline". The row formats this date itself, so
      // an empty string would render as the epoch; absent is the honest value.
      due: t.due || undefined,
      // Derived in SQL against the database's own current_date, so it cannot
      // drift from the due date the same row reports.
      overdue: Boolean(t.due && t.due < new Date().toISOString().slice(0, 10) && t.status === "pending"),
      // Typed subjects let every review item return to the exact evidence it
      // was filed from. Older rows have no subject fields and remain valid.
      subject,
    };
  });
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
  const items = res.reviewItems?.items ?? [];
  const total = res.reviewItems?.totalCount ?? items.length;
  if (!total) return null;
  const label = (kind: string) => ({ fact: "Fact", decision: "Decision", answer: "Answer",
    finding: "Finding", change: "Change", workflow: "Approval", task: "Task" } as Record<string, string>)[kind] ?? kind;
  return {
    title: "Review queue",
    tags: [...new Set(items.map((row) => label(row.kind)))].sort(),
    people: [...new Set(items.map((row) => row.assignee ? row.assignee.split(/\s+/).slice(0, 2).map((x) => x[0]?.toUpperCase()).join("") : "").filter(Boolean))],
    statValue: String(total),
    statLabel: "open",
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
  /* Built once per RESPONSE, not once per render. The board adopts each new
     server read via `seenTasks !== data.tasks` (array identity) and drops the
     optimistic moves that read overwrote — so a fresh `mapTasks` per render
     would discard every drag and every close the instant anything re-rendered
     the page. */
  const data = useMemo(
    () => buildTasks(
      q.data ? mapTasks(q.data) : [],
      q.data ? mapStrip(q.data) : null,
      draft,
      q.data ? mapAssignees(q.data) : [],
    ),
    [q.data, draft],
  );
  return {
    data,
    loading: q.loading,
    error: q.error ? (q.errorText ?? "Tasks are temporarily unavailable.") : null,
  };
}
