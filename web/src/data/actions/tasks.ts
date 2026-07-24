/* Tasks inbox writes.
 *
 * Three controls, three mutations. `setTaskDue` also exists server-side but the
 * board draws no due-date control, so no handler claims it: an action nothing
 * can reach is as dishonest as a button that reaches nothing.
 */

import type { TasksActions } from "@mari-design/components/pages/TasksPage";
import { mutate } from "../actions";

const SET_DONE = `mutation SetTaskDone($id: Int!, $done: Boolean!) {
  setTaskDone(id: $id, done: $done)
}`;

const CREATE = `mutation CreateTask($title: String!, $kind: String!, $kindLabel: String!) {
  createTask(title: $title, kind: $kind, kindLabel: $kindLabel)
}`;

const CLEAR_DONE = `mutation ClearDoneTasks { clearDoneTasks }`;

export function tasksActions(): TasksActions {
  return {
    setDone: async ({ id, done }) => { await mutate(SET_DONE, { id, done }); },
    // No `assignee`: the mutation defaults it to the signed-in person, which is
    // who is standing at the composer. Passing a guess would file the task to
    // someone the console never asked about.
    create: async ({ title, kind, kindLabel }) => { await mutate(CREATE, { title, kind, kindLabel }); },
    clearDone: async () => { await mutate(CLEAR_DONE); },
  };
}
