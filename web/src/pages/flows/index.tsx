// Flows — Zapier-style vertical pipeline editor over the real workflow engine.
// Views: list (default) → editor (vertical step pipeline + config panel).
// Runs open in a slide-in Drawer, deep-linkable via ?run=<number>.

import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import * as Ic from "../../components/icons";
import { Button, Chip, Drawer, EmptyState, Field, Input, Menu, MenuItem, PageHeader, Select, Spinner, useToast } from "../../components/ui";
import { gql, useQuery } from "../../lib/api";
import "../flows.css";
import {
  deepCopy, describeTrigger, fmtEvery, fmtStarted, hasTrigger,
  isDry, KIND_META, normStatus, TEMPLATE_OUTCOME, triggerSummary,
  type EditorState, type Run, type SourceOpt, type Workflow,
} from "./data";
import { PipelineEditor } from "./PipelineEditor";
import { RunHistory, RunStatusChip } from "./RunHistory";
import { RunPanel } from "./RunPanel";

// ————— trigger editor —————
// Compact Drawer over setWorkflowTrigger: On [Manual only | Document added |
// Document changed | Schedule]. Document events take optional source / tag /
// path-glob filters; Schedule takes an every-minutes cadence instead.

function TriggerEditor({ workflow, sources, onClose, onSaved }: {
  workflow: Workflow; sources: SourceOpt[]; onClose: () => void; onSaved: () => void;
}) {
  const toast = useToast();
  const t = workflow.trigger ?? {};
  const [on, setOn] = useState<string>(t.on ?? "");
  const [sourceId, setSourceId] = useState<string>(t.source_id != null ? String(t.source_id) : "");
  const [tag, setTag] = useState<string>(t.tag ?? "");
  const [glob, setGlob] = useState<string>(t.path_glob ?? "");
  const [every, setEvery] = useState<string>(t.every_minutes != null ? String(t.every_minutes) : "10");
  const [saving, setSaving] = useState(false);

  const everyN = Math.floor(Number(every));
  const everyBad = on === "schedule" && !(everyN >= 1 && everyN <= 10080);

  const save = async () => {
    if (saving || everyBad) return;
    setSaving(true);
    // setWorkflowTrigger takes the trigger as a JSON *string*; "{}" = manual-only.
    const trigger = !on
      ? "{}"
      : on === "schedule"
        ? JSON.stringify({ on, every_minutes: everyN })
        : JSON.stringify({
            on,
            source_id: sourceId ? Number(sourceId) : null,
            tag: tag.trim() || null,
            path_glob: glob.trim() || null,
          });
    const d = await gql(
      `mutation($id: Int!, $t: String!) { setWorkflowTrigger(workflowId: $id, trigger: $t) }`,
      { id: workflow.id, t: trigger },
    );
    setSaving(false);
    if (!d) { toast("Couldn't reach the API — trigger not saved.", "error"); return; }
    toast(
      on === "schedule"
        ? `Trigger saved — "${workflow.name}" now runs ${fmtEvery(everyN).toLowerCase()}.`
        : on
          ? `Trigger saved — "${workflow.name}" now runs on document events.`
          : `"${workflow.name}" is manual-only again.`,
      "success",
    );
    onSaved();
    onClose();
  };

  return (
    <Drawer
      open
      onOpenChange={(o) => { if (!o) onClose(); }}
      title="Trigger"
      meta={workflow.name}
      footer={(
        <>
          <Button variant="primary" disabled={saving || everyBad} onClick={save}>
            {saving ? "Saving…" : "Save trigger"}
          </Button>
          <span className="card__spacer" />
          <Button onClick={onClose}>Cancel</Button>
        </>
      )}
    >
      <div style={{ display: "grid", gap: 12 }}>
        <Field label="On" hint={on ? undefined : "Manual only — the flow runs when you press Run."}>
          <Select value={on} onChange={(e) => setOn(e.target.value)}>
            <option value="">Manual only</option>
            <option value="document_added">Document added</option>
            <option value="document_changed">Document changed</option>
            <option value="schedule">Schedule</option>
          </Select>
        </Field>
        {on === "schedule" && (
          <>
            <Field
              label="Every (minutes)"
              hint={everyBad ? "Enter 1–10080 minutes (10080 = a week)." : `Runs ${fmtEvery(everyN).toLowerCase()} — presets: 10 (ten min) · 60 (hourly) · 1440 (daily) · 10080 (weekly).`}
            >
              <Input
                type="number" min={1} max={10080} value={every}
                onChange={(e) => setEvery(e.target.value)}
              />
            </Field>
            <div className="card__hint">
              The scheduler checks twice a minute and never starts a run while the previous one is still going.
            </div>
          </>
        )}
        {on && on !== "schedule" && (
          <>
            <Field label="Source" hint="Only fire for docs from this source.">
              <Select value={sourceId} onChange={(e) => setSourceId(e.target.value)}>
                <option value="">Any source</option>
                {sources.map((s) => <option key={s.id} value={String(s.id)}>{s.name}</option>)}
              </Select>
            </Field>
            <Field label="Tag" hint="Only fire for docs carrying this tag.">
              <Input value={tag} placeholder="e.g. customer-facing" onChange={(e) => setTag(e.target.value)} />
            </Field>
            <Field label="Path glob" hint="Only fire for docs whose path matches, e.g. docs/**">
              <Input className="mono" value={glob} placeholder="docs/**" onChange={(e) => setGlob(e.target.value)} />
            </Field>
            <div className="card__hint">
              Filters are optional and combine — leave them empty to fire on every matching event.
            </div>
          </>
        )}
      </div>
    </Drawer>
  );
}

export default function FlowsPage() {
  const [flash, setFlash] = useState<string | null>(null);
  const [panelNote, setPanelNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // editor — the draft itself lives inside PipelineEditor; this is the seed
  const [editor, setEditor] = useState<EditorState | null>(null);
  const [editorNonce, setEditorNonce] = useState(0);

  // document-trigger editor (Drawer) — which workflow is being edited
  const [trigEdit, setTrigEdit] = useState<Workflow | null>(null);

  const workflowsQ = useQuery<Workflow[]>(
    `{ workflows { id name description color pinned status nodes trigger } }`,
    { map: (d: any) => d.workflows ?? [] });
  const runsQ = useQuery<Run[]>(
    `{ workflowRuns { id workflowId workflowName number status started duration progress stats rows triggeredBy } }`,
    { map: (d: any) => d.workflowRuns ?? [] });
  const sourcesQ = useQuery<SourceOpt[]>(`{ sourcePulse { id provider name } }`, { map: (d: any) => d.sourcePulse ?? [] });
  const tagDefsQ = useQuery<{ tag: string; label: string }[]>(`{ tagDefs { tag label } }`, { map: (d: any) => d.tagDefs ?? [] });
  const membersQ = useQuery<{ name: string }[]>(`{ members { name } }`, { map: (d: any) => d.members ?? [] });
  const sitesQ = useQuery<{ id: number; name: string }[]>(`{ sites { id name } }`, { map: (d: any) => d.sites ?? [] });

  const workflows = workflowsQ.data ?? [];
  const runs = useMemo(() => runsQ.data ?? [], [runsQ.data]);
  const refetchWorkflows = workflowsQ.refetch;
  const refetchRuns = runsQ.refetch;

  const runsSorted = useMemo(() => [...runs].sort((a, b) => b.id - a.id), [runs]);

  // run panel — deep-linkable via ?run=<number>
  const [sp, setSp] = useSearchParams();
  const runParam = sp.get("run");
  const panelRun = runParam == null ? null : runsSorted.find((r) => String(r.number) === runParam) ?? null;
  const openRun = (n: number) => {
    setPanelNote(null);
    setSp((prev) => { const p = new URLSearchParams(prev); p.set("run", String(n)); return p; });
  };
  const closeRun = () => {
    setPanelNote(null);
    setSp((prev) => { const p = new URLSearchParams(prev); p.delete("run"); return p; });
  };

  // poll while any run is executing, while the open panel shows a live/waiting
  // run (so an approval elsewhere shows up), or while a just-started run
  // hasn't appeared in the list yet
  const pollNeeded =
    runs.some((r) => r.status === "running") ||
    (runParam != null && (!panelRun || panelRun.status === "running" || panelRun.status === "waiting"));
  useEffect(() => {
    if (!pollNeeded) return;
    const t = window.setInterval(() => refetchRuns(), 3500);
    return () => window.clearInterval(t);
  }, [pollNeeded, refetchRuns]);

  const say = (msg: string) => {
    setFlash(msg);
    window.setTimeout(() => setFlash(null), 4500);
  };

  // ————— mutations —————

  const doRun = async (workflowId: number, dry: boolean): Promise<number | null> => {
    setBusy(true);
    const d = await gql<{ runWorkflow: number }>(
      `mutation($id: Int!, $dry: Boolean) { runWorkflow(workflowId: $id, dryRun: $dry) }`,
      { id: workflowId, dry });
    setBusy(false);
    refetchRuns();
    if (d?.runWorkflow == null) { say("Couldn't reach the API — run not started."); return null; }
    openRun(d.runWorkflow);
    return d.runWorkflow;
  };

  const approve = async (run: Run) => {
    setBusy(true);
    const d = await gql<{ approveRun: boolean }>(`mutation($id: Int!) { approveRun(runId: $id) }`, { id: run.id });
    setBusy(false);
    refetchRuns();
    setPanelNote(d?.approveRun ? "Approved — the run is resuming." : "Couldn't reach the API — not approved.");
  };

  const rerun = async (run: Run, dry: boolean) => {
    const num = await doRun(run.workflowId, dry);
    if (num != null) setPanelNote(`Started run #${num}${dry ? " as a test" : ""} — this panel is now showing it.`);
  };

  const toggleStatus = async (w: Workflow) => {
    const next = w.status === "active" ? "paused" : "active";
    const d = await gql(`mutation($id: Int!, $s: String!) { setWorkflowStatus(id: $id, status: $s) }`, { id: w.id, s: next });
    if (!d) { say("Couldn't reach the API — flow not updated."); return; }
    refetchWorkflows();
  };

  const deleteFlow = async (w: Workflow) => {
    if (!window.confirm(`Delete "${w.name}"? Its run history stays, but the flow is gone for the whole team.`)) return;
    const d = await gql(`mutation($id: Int!) { deleteWorkflow(id: $id) }`, { id: w.id });
    if (!d) { say("Couldn't reach the API — flow not deleted."); return; }
    refetchWorkflows();
  };

  const duplicateFlow = async (w: Workflow) => {
    const d = await gql<{ saveWorkflow: number }>(
      `mutation($n: String!, $d: String!, $s: JSON!) { saveWorkflow(name: $n, description: $d, steps: $s) }`,
      { n: `${w.name} copy`, d: w.description, s: w.nodes ?? [] });
    if (d?.saveWorkflow == null) { say("Couldn't reach the API — flow not duplicated."); return; }
    refetchWorkflows();
    say(`Duplicated as "${w.name} copy".`);
  };

  const saveFlow = async (state: EditorState): Promise<number | null> => {
    const d = await gql<{ saveWorkflow: number }>(
      `mutation($n: String!, $d: String!, $s: JSON!, $id: Int) { saveWorkflow(name: $n, description: $d, steps: $s, id: $id) }`,
      { n: state.name.trim() || "Untitled flow", d: state.description, s: state.steps, id: state.id });
    if (d?.saveWorkflow == null) { say("Couldn't reach the API — flow not saved."); return null; }
    refetchWorkflows();
    return d.saveWorkflow;
  };

  // ————— editor openers —————

  const openEditor = (w: Workflow) => {
    setEditor({ id: w.id, name: w.name, description: w.description, steps: deepCopy(w.nodes ?? []) });
    setEditorNonce((n) => n + 1);
  };
  const applyTemplate = (w: Workflow) => {
    setEditor({ id: null, name: `${w.name} (new)`, description: w.description, steps: deepCopy(w.nodes ?? []) });
    setEditorNonce((n) => n + 1);
  };
  const newFlow = () => {
    setEditor({
      id: null, name: "Untitled flow", description: "",
      steps: [{ kind: "trigger", label: KIND_META.trigger.defLabel, config: { ...KIND_META.trigger.defConfig } }],
    });
    setEditorNonce((n) => n + 1);
  };

  // ————— views —————

  const listView = (
    <>
      <PageHeader
        title="Flows"
        description="Automation over your knowledge: when something happens, Mari does editorial work, checks it, then delivers it — every action attributed to its run."
        actions={<Button variant="primary" onClick={newFlow}><Ic.Plus size={14} /> New flow</Button>}
      />

      {workflowsQ.loading && (
        <div className="card" style={{ display: "grid", placeItems: "center", minHeight: 140 }}>
          <Spinner size="sm" label="Loading flows" />
        </div>
      )}
      {workflowsQ.error && !workflowsQ.data && (
        <div className="card">
          <EmptyState icon={<Ic.Flow size={20} />}>API offline — flows unavailable.</EmptyState>
        </div>
      )}

      {workflowsQ.data && (
      <>
      <div className="fl-gallery">
        {workflows.slice(0, 4).map((w) => (
          <div key={w.id} className="card fl-tpl">
            <span className="fl-tpl__eyebrow"><span className="fl-tpl__dot" style={{ background: w.color }} /> Template</span>
            <span className="fl-tpl__outcome">{TEMPLATE_OUTCOME[w.name] ?? w.description}</span>
            <span className="fl-tpl__mech">{(w.nodes ?? []).map((n) => n.label).join(" → ")}</span>
            <button className="linklike" onClick={() => applyTemplate(w)}>Use template →</button>
          </div>
        ))}
      </div>

      <div className="card fl-list">
        {workflows.map((w) => {
          const wfRuns = runsSorted.filter((r) => r.workflowId === w.id);
          const last = wfRuns[0];
          return (
            <div key={w.id} className={`fl-row${w.status === "paused" ? " is-paused" : ""}`}>
              <button
                className={`fl-toggle${w.status === "active" ? " on" : ""}`}
                title={w.status === "active" ? "Active — click to pause" : "Paused — click to enable"}
                aria-label={`${w.name}: ${w.status}`}
                onClick={() => toggleStatus(w)}
              />
              <div style={{ minWidth: 0 }}>
                <button className="fl-row__name" onClick={() => openEditor(w)}>{w.name}</button>
                <div className="fl-row__desc">{w.description}</div>
              </div>
              <div style={{ minWidth: 0 }}>
                <div className="fl-row__trigger">
                  <span className="fl-when">When</span>
                  <span title={triggerSummary(w)}>{triggerSummary(w)}</span>
                </div>
                <Chip
                  className="fl-trigchip"
                  tone={hasTrigger(w.trigger) ? "green" : "faint"}
                  dot={hasTrigger(w.trigger)}
                  title={`${describeTrigger(w.trigger, sourcesQ.data ?? [])} — click to edit`}
                  onClick={() => setTrigEdit(w)}
                >
                  {describeTrigger(w.trigger, sourcesQ.data ?? [])}
                </Chip>
              </div>
              <div className="fl-col-hide">
                {last ? (
                  <>
                    <RunStatusChip status={last.status} dry={isDry(last)} />
                    <div className="fl-row__last">{fmtStarted(last.started)}</div>
                  </>
                ) : (
                  <span className="fl-row__last">Never run</span>
                )}
              </div>
              <div className="fl-dots fl-col-hide">
                {wfRuns.slice(0, 5).reverse().map((r) => (
                  <button
                    key={r.id}
                    className={`fl-dot fl-dot--${normStatus(r.status)}`}
                    title={`#${r.number} · ${r.status}${isDry(r) ? " · dry run" : ""}`}
                    onClick={() => openRun(r.number)}
                  />
                ))}
              </div>
              <div className="fl-row__actions">
                <Button disabled={busy} onClick={() => doRun(w.id, false)}><Ic.Play size={13} /> Run</Button>
                <Button disabled={busy} title="Runs for real, but side effects become previews" onClick={() => doRun(w.id, true)}>Test run</Button>
                <Menu trigger={
                  <button className="kebab" aria-label={`More actions for ${w.name}`}><Ic.Kebab size={15} /></button>
                }>
                  <MenuItem icon={<Ic.Pencil size={14} />} onSelect={() => openEditor(w)}>Edit</MenuItem>
                  <MenuItem icon={<Ic.Bell size={14} />} onSelect={() => setTrigEdit(w)}>Edit trigger…</MenuItem>
                  <MenuItem icon={<Ic.Clipboard size={14} />} onSelect={() => duplicateFlow(w)}>Duplicate</MenuItem>
                  <MenuItem danger icon={<Ic.Trash size={14} />} onSelect={() => window.setTimeout(() => deleteFlow(w), 0)}>Delete…</MenuItem>
                </Menu>
              </div>
            </div>
          );
        })}
        {workflows.length === 0 && (
          <EmptyState icon={<Ic.Flow size={20} />}>No flows yet — start from a template or create one.</EmptyState>
        )}
      </div>
      </>
      )}

      <RunHistory runs={runsSorted} onOpen={openRun} />
    </>
  );

  return (
    <>
      {flash && <div className="card fl-toast">{flash}</div>}
      {editor ? (
        <PipelineEditor
          key={editorNonce}
          initial={editor}
          workflows={workflows}
          busy={busy}
          runs={runsSorted}
          tagDefs={tagDefsQ.data ?? []}
          members={membersQ.data ?? []}
          sites={sitesQ.data ?? []}
          sources={sourcesQ.data ?? []}
          onBack={() => setEditor(null)}
          onToggleStatus={toggleStatus}
          onSave={saveFlow}
          onRun={doRun}
          onOpenRun={openRun}
        />
      ) : listView}
      {trigEdit && (
        <TriggerEditor
          workflow={trigEdit}
          sources={sourcesQ.data ?? []}
          onClose={() => setTrigEdit(null)}
          onSaved={refetchWorkflows}
        />
      )}
      {runParam != null && (
        <RunPanel
          run={panelRun}
          number={runParam}
          onClose={closeRun}
          onApprove={approve}
          onRerun={rerun}
          busy={busy}
          note={panelNote}
        />
      )}
    </>
  );
}
