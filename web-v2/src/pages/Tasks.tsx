// Full task list — reachable only from the Overview's "View all tasks" link.

import { useEffect, useState } from "react";
import * as Ic from "../components/icons";
import { Pill } from "../components/shared";
import { Avatar, EmptyState, IconRing, PageHeader, Spinner } from "../components/ui";
import { gql } from "../lib/api";

type Task = { id?: number; title: string; who: string; tint: number; kind: string; kindLabel: string; done: boolean };

const TASKS_QUERY = `{ tasks { id title assigneeInitials assigneeTint kind kindLabel done } }`;

const KINDS = [
  { kind: "approval", label: "Approval" },
  { kind: "factcheck", label: "Fact check" },
  { kind: "stale", label: "Stale" },
];

export default function TasksPage() {
  const [tasks, setTasks] = useState<Task[] | null>(null); // null → still loading
  const [loadError, setLoadError] = useState(false);
  const [title, setTitle] = useState("");
  const [kind, setKind] = useState(KINDS[0].kind);
  const [adding, setAdding] = useState(false);

  const load = () =>
    gql(TASKS_QUERY).then((d: any) => {
      if (d?.tasks) {
        setLoadError(false);
        setTasks(
          d.tasks.map((t: any) => ({
            id: t.id, title: t.title, who: t.assigneeInitials, tint: t.assigneeTint,
            kind: t.kind, kindLabel: t.kindLabel, done: t.done,
          }))
        );
      } else {
        setLoadError(true);
      }
    });

  useEffect(() => { load(); }, []);

  const toggle = (t: Task) => {
    const done = !t.done;
    setTasks((ts) => ts && ts.map((x) => (x === t ? { ...x, done } : x)));
    if (t.id != null) gql(`mutation($id: Int!, $done: Boolean!) { setTaskDone(id: $id, done: $done) }`, { id: t.id, done });
  };

  const clearDone = async () => {
    setTasks((ts) => ts && ts.filter((t) => !t.done));
    await gql(`mutation { clearDoneTasks }`);
    load();
  };

  const addTask = async () => {
    const t = title.trim();
    if (!t || adding) return;
    setAdding(true);
    const kindLabel = KINDS.find((k) => k.kind === kind)?.label ?? "Approval";
    const ok = await gql(
      `mutation($title: String!, $kind: String, $kindLabel: String, $assignee: String) {
        createTask(title: $title, kind: $kind, kindLabel: $kindLabel, assignee: $assignee)
      }`,
      { title: t, kind, kindLabel, assignee: "Maya M." },
    );
    if (ok) await load();
    else setTasks((ts) => [...(ts ?? []), { title: t, who: "MM", tint: 1, kind, kindLabel, done: false }]);
    setTitle("");
    setAdding(false);
  };

  const loading = tasks === null && !loadError;
  const offline = tasks === null && loadError;
  const open = (tasks ?? []).filter((t) => !t.done);
  const done = (tasks ?? []).filter((t) => t.done);

  const row = (t: Task) => (
    <div className="review__item" key={t.id ?? t.title}>
      <button
        className={`review__check${t.done ? " on" : ""}`}
        onClick={() => toggle(t)}
        aria-label={t.done ? "Mark undone" : "Mark done"}
      >
        <Ic.Check size={11} strokeWidth={2.4} />
      </button>
      <span className={`review__text${t.done ? " done" : ""}`}>{t.title}</span>
      <Avatar name={t.who} initials={t.who} tint={t.tint as 1 | 2 | 3 | 4} size="sm" />
      <Pill kind={t.kind} text={t.kindLabel} />
    </div>
  );

  return (
    <>
      <PageHeader title="Tasks" backLink={{ to: "/", label: "Overview" }} />

      <div className="tasks-wrap">
        {/* new task composer */}
        <div className="card tasks-composer">
          <input
            className="input"
            placeholder="New task — e.g. Re-verify SLA uptime fact"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && addTask()}
          />
          <select className="select" value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Task kind">
            {KINDS.map((k) => <option key={k.kind} value={k.kind}>{k.label}</option>)}
          </select>
          <button className="btn btn--primary" onClick={addTask} disabled={adding || !title.trim()}>
            <Ic.Plus size={14} /> Add task
          </button>
        </div>

        {/* open */}
        <div className="card review">
          <div className="card__head">
            <IconRing><Ic.Clipboard size={16} /></IconRing>
            <h3 className="card__title">Open</h3>
            <span className="card__spacer" />
            <span className="countbubble">{open.length}</span>
          </div>
          <div className="review__list tasks-list">
            {loading && <div style={{ display: "grid", placeItems: "center", minHeight: 90 }}><Spinner size="sm" /></div>}
            {offline && <EmptyState>API offline — tasks unavailable.</EmptyState>}
            {open.map(row)}
            {tasks !== null && open.length === 0 && <EmptyState>Nothing open — all caught up.</EmptyState>}
          </div>
        </div>

        {/* done */}
        <div className="card review">
          <div className="card__head">
            <IconRing><Ic.CheckCircle size={16} /></IconRing>
            <h3 className="card__title">Done</h3>
            <span className="card__spacer" />
            <span className="countbubble">{done.length}</span>
            {done.length > 0 && (
              // canonical clear-done affordance — matches Overview's linklike "Clear done"
              <button className="review__clear" onClick={clearDone}>
                <Ic.Trash size={13} /> Clear done
              </button>
            )}
          </div>
          <div className="review__list tasks-list">
            {loading && <div style={{ display: "grid", placeItems: "center", minHeight: 90 }}><Spinner size="sm" /></div>}
            {offline && <EmptyState>API offline — tasks unavailable.</EmptyState>}
            {done.map(row)}
            {tasks !== null && done.length === 0 && <EmptyState>No completed tasks yet.</EmptyState>}
          </div>
        </div>
      </div>
    </>
  );
}
