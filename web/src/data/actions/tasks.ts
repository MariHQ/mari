/* Tasks inbox writes.
 *
 * The composer now carries the owner and the deadline the server has always
 * been able to store (`createTask` takes both), so a task filed from the board
 * lands with the person and the date whoever filed it chose instead of always
 * defaulting to the signed-in account.
 */

import type { TaskSubject, TasksActions } from "@mari-design/components/pages/TasksPage";
import { mutate, type ActionContext } from "../actions";

const SET_DONE = `mutation SetTaskDone($id: Int!, $done: Boolean!) {
  setTaskDone(id: $id, done: $done)
}`;

/* Two documents rather than one with a nullable `assignee`: the mutation's
   assignee argument is non-null with a server-side default (the signed-in
   person), so "leave it to the server" has to be expressed by not naming the
   argument at all — passing null would be rejected outright. */
const CREATE = `mutation CreateTask($title: String!, $kind: String!, $kindLabel: String!, $due: String) {
  createTask(title: $title, kind: $kind, kindLabel: $kindLabel, due: $due)
}`;

const CREATE_ASSIGNED = `mutation CreateTask($title: String!, $kind: String!, $kindLabel: String!, $assignee: String!, $due: String) {
  createTask(title: $title, kind: $kind, kindLabel: $kindLabel, assignee: $assignee, due: $due)
}`;

const CLEAR_DONE = `mutation ClearDoneTasks { clearDoneTasks }`;

export function tasksActions({ navigate }: ActionContext): TasksActions {
  return {
    setDone: async ({ id, done }) => { await mutate(SET_DONE, { id, done }); },

    /* `assignee` and `due` are only sent when the composer collected them.
       Omitted, the mutation files the task to the signed-in person with no
       deadline, which is what it has always done.

       `priority` is never sent: `tasks` has no column for one, so the board
       carries no priority vocabulary and the composer draws no such control.

       Nothing is returned. `createTask` answers with a boolean rather than the
       row it wrote, so there is no server-issued id to hand back — and making
       one up is exactly what the return value exists to prevent. The board
       falls back to re-reading, which shows the real row. */
    create: async ({ title, kind, kindLabel, assignee, due }) => {
      const vars = { title, kind, kindLabel, due: due || null };
      await (assignee
        ? mutate(CREATE_ASSIGNED, { ...vars, assignee })
        : mutate(CREATE, vars));
    },

    clearDone: async () => { await mutate(CLEAR_DONE); },

    // New rows carry their own stable in-app address. `openDoc` remains for
    // rows produced before typed subjects existed and for older API servers.
    openSubject: (subject: TaskSubject) => {
      if (subject.href) navigate(subject.href);
      else if (subject.type === "document") navigate(`/knowledge/doc?id=${encodeURIComponent(subject.id)}`);
    },
    openDoc: (docId: string) => navigate(`/knowledge/doc?id=${encodeURIComponent(docId)}`),
  };
}
