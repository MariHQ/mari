// Flow editor — vertical step pipeline on a dashed spine + sticky config panel.
// Owns the draft (name/description/steps/dirty); the page owns data + mutations.

import { Fragment, ReactNode, useEffect, useState } from "react";
import * as Ic from "../../components/icons";
import { Button, Field, Input, Select } from "../../components/ui";
import { gql } from "../../lib/api";
import {
  CONDITION_FIELDS, KIND_ICON, KIND_META, PICKER_SECTIONS, REFINE_SKILLS,
  SECTION_META, SECTION_OF, stepSummary, TASK_KINDS,
  type EditorState, type Run, type SourceOpt, type Step, type StepKind, type Workflow,
} from "./data";
import { RunHistory } from "./RunHistory";

const stepIcon = (k: StepKind) => {
  const I = KIND_ICON[k];
  return <I size={13} />;
};

const llmBadge = (
  <span className="fl-llm"><Ic.Sparkle size={10} /> LLM — runs the local model, slower</span>
);

// ————— step picker —————

function StepPicker({ onPick, onClose }: { onPick: (k: StepKind) => void; onClose: () => void }) {
  return (
    <div className="card fl-picker">
      <div className="fl-picker__head">
        Add a step
        <span className="card__spacer" />
        <button className="kebab" onClick={onClose} aria-label="Close picker"><Ic.Close size={13} /></button>
      </div>
      {PICKER_SECTIONS.map(({ sec, kinds }) => (
        <div key={sec}>
          <div className="fl-picker__sec" style={{ color: SECTION_META[sec].color }}>
            {SECTION_META[sec].title} — {SECTION_META[sec].tagline}
          </div>
          <div className="fl-picker__grid">
            {kinds.map((k) => (
              <button key={k} className="fl-pick" onClick={() => onPick(k)}>
                <b>{stepIcon(k)} {KIND_META[k].name}</b>
                <span>{KIND_META[k].desc}</span>
                {KIND_META[k].llm && llmBadge}
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

// ————— config panel (right column of the editor) —————

function ConfigPanel({ step, onLabel, onConfig, tagDefs, members, sites, sources }: {
  step: Step;
  onLabel: (v: string) => void;
  onConfig: (key: string, v: any) => void;
  tagDefs: { tag: string; label: string }[];
  members: { name: string }[];
  sites: { id: number; name: string }[];
  sources: SourceOpt[];
}) {
  const meta = KIND_META[step.kind];
  const sec = SECTION_OF[step.kind];
  const c = step.config ?? {};

  // live trigger hydration — debounced search over the scope query
  const query = step.kind === "trigger" || step.kind === "fetch_docs" ? String(c.query ?? "") : "";
  const [hyd, setHyd] = useState<{ q: string; count: number; titles: string[] } | null>(null);
  // clear stale hydration synchronously when the query empties (render-time, no effect)
  if (!query.trim() && hyd !== null) setHyd(null);
  useEffect(() => {
    const q = query.trim();
    if (!q) return;
    const t = window.setTimeout(async () => {
      const d = await gql<{ search: { id: number; title: string }[] }>(
        `query($q: String!) { search(query: $q, k: 25) { id title } }`, { q });
      if (d) setHyd({ q, count: d.search.length, titles: d.search.slice(0, 3).map((s) => s.title) });
    }, 400);
    return () => window.clearTimeout(t);
  }, [query]);

  const field = (label: string, input: ReactNode) => <Field label={label}>{input}</Field>;
  const memberSelect = (key: string, allowNone = false) => (
    <Select value={c[key] ?? ""} onChange={(e) => onConfig(key, e.target.value || undefined)}>
      {allowNone && <option value="">— anyone / unassigned —</option>}
      {!allowNone && !c[key] && <option value="">— choose —</option>}
      {members.map((m) => <option key={m.name} value={m.name}>{m.name}</option>)}
    </Select>
  );

  // Propose an event from an actually-connected GitHub source, not a stock example.
  const ghSource = sources.find((s) => s.provider === "github");
  const triggerEventHint = ghSource ? `e.g. GitHub PR to ${ghSource.name}` : "e.g. GitHub PR merged";

  return (
    <div className="card fl-cfg">
      <div className="fl-cfg__kind">
        <span className={`fl-chip ${SECTION_META[sec].cls}`}>{SECTION_META[sec].title}</span>
        <span className="fl-cfg__title">{meta.name}</span>
      </div>
      <div className="fl-cfg__desc">{meta.desc}</div>
      {meta.llm && llmBadge}

      {field("Label", <Input type="text" value={step.label} onChange={(e) => onLabel(e.target.value)} placeholder={meta.defLabel} />)}

      {step.kind === "trigger" && (
        <>
          {field("Event", <Input type="text" value={c.label ?? ""} onChange={(e) => onConfig("label", e.target.value)} placeholder={triggerEventHint} />)}
          {field("Scope query", <Input type="text" value={c.query ?? ""} onChange={(e) => onConfig("query", e.target.value)} placeholder="which docs does this watch? e.g. authentication" />)}
        </>
      )}

      {step.kind === "fetch_docs" && (
        <>
          {field("Search query", <Input type="text" value={c.query ?? ""} onChange={(e) => onConfig("query", e.target.value)} placeholder="e.g. authentication" />)}
          {field("Tag filter", (
            <Select value={c.tag ?? ""} onChange={(e) => onConfig("tag", e.target.value || undefined)}>
              <option value="">— any tag —</option>
              {tagDefs.map((t) => <option key={t.tag} value={t.tag}>{t.label}</option>)}
            </Select>
          ))}
          {field("Max documents (k)", <Input type="number" min={1} max={25} value={c.k ?? 3} onChange={(e) => onConfig("k", Math.max(1, Number(e.target.value) || 1))} />)}
        </>
      )}

      {(step.kind === "trigger" || step.kind === "fetch_docs") && query.trim() && (
        hyd && hyd.q === query.trim() ? (
          <div className={`fl-hydration${hyd.count === 0 ? " fl-hydration--empty" : ""}`}>
            {hyd.count === 0 ? (
              <>Currently matches no documents — the flow would do nothing today.</>
            ) : (
              <>
                Currently matches {hyd.count >= 25 ? "25+" : hyd.count} documents{step.kind === "fetch_docs" ? ` — top ${Math.min(c.k ?? 3, hyd.count)} enter the run` : ""}
                <em>{hyd.titles.join(" · ")}{hyd.count > 3 ? " …" : ""}</em>
              </>
            )}
          </div>
        ) : (
          <div className="fl-hydration fl-hydration--empty">Checking the index…</div>
        )
      )}

      {step.kind === "refine" && field("Mari skill", (
        <Select value={c.skill ?? "tighten"} onChange={(e) => onConfig("skill", e.target.value)}>
          {REFINE_SKILLS.map((s) => <option key={s} value={s}>{s}</option>)}
        </Select>
      ))}

      {step.kind === "tag" && field("Tag to apply", (
        <Select value={c.tag ?? "needs-review"} onChange={(e) => onConfig("tag", e.target.value)}>
          {tagDefs.map((t) => <option key={t.tag} value={t.tag}>{t.label}</option>)}
        </Select>
      ))}

      {step.kind === "condition" && (
        <>
          {field("Field", (
            <Select value={c.field ?? "contradictions"} onChange={(e) => onConfig("field", e.target.value)}>
              {CONDITION_FIELDS.map((f) => <option key={f} value={f}>{f}</option>)}
            </Select>
          ))}
          {field("Is greater than", <Input type="number" min={0} value={c.greater_than ?? 0} onChange={(e) => onConfig("greater_than", Math.max(0, Number(e.target.value) || 0))} />)}
        </>
      )}

      {step.kind === "create_task" && (
        <>
          {field("Task title", <Input type="text" value={c.title ?? ""} onChange={(e) => onConfig("title", e.target.value)} placeholder="e.g. Resolve contradictions" />)}
          {field("Assignee", memberSelect("assignee", true))}
          {field("Kind", (
            <Select value={c.kind ?? "review"} onChange={(e) => {
              onConfig("kind", e.target.value);
              onConfig("kind_label", TASK_KINDS.find(([k]) => k === e.target.value)?.[1] ?? e.target.value);
            }}>
              {TASK_KINDS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </Select>
          ))}
          <label className="fl-checkline">
            <input type="checkbox" checked={!!c.only_if_branch} onChange={(e) => onConfig("only_if_branch", e.target.checked || undefined)} />
            <span>Only on the yes-branch<i>Runs only when the condition above it passes; otherwise skipped.</i></span>
          </label>
        </>
      )}

      {step.kind === "approval" && field("Approver", memberSelect("assignee"))}

      {step.kind === "notify" && (
        <>
          {field("Notify", memberSelect("user", true))}
          {field("Message", <Input type="text" value={c.text ?? ""} onChange={(e) => onConfig("text", e.target.value)} placeholder="e.g. Weekly digest is ready" />)}
          {field("Detail", <Input type="text" value={c.detail ?? ""} onChange={(e) => onConfig("detail", e.target.value)} placeholder="one line of context" />)}
        </>
      )}

      {step.kind === "deploy_site" && field("Site", (
        <Select value={String(c.site_id ?? sites[0]?.id ?? 1)} onChange={(e) => onConfig("site_id", Number(e.target.value))}>
          {sites.map((s) => <option key={s.id} value={String(s.id)}>{s.name}</option>)}
        </Select>
      ))}

      {step.kind === "sync_source" && field("Source", (
        <Select value={String(c.source_id ?? "")} onChange={(e) => onConfig("source_id", Number(e.target.value) || 0)}>
          {!c.source_id && <option value="">— choose a source —</option>}
          {sources.map((s) => <option key={s.id} value={String(s.id)}>{s.name}</option>)}
        </Select>
      ))}

      {(step.kind === "fact_check" || step.kind === "summarize" || step.kind === "derive_links") && (
        <div className="card__hint" style={{ marginTop: 12 }}>
          No configuration — this step works over whatever the fetch step brought into the run.
        </div>
      )}

      {step.kind === "refresh_digest" && (
        <div className="card__hint" style={{ marginTop: 12 }}>
          No configuration — regenerates the digest from the most recent documents and facts.
        </div>
      )}
    </div>
  );
}

// ————— editor —————

export function PipelineEditor({ initial, workflows, busy, runs, tagDefs, members, sites, sources, onBack, onToggleStatus, onSave, onRun, onOpenRun }: {
  initial: EditorState;
  workflows: Workflow[];
  busy: boolean;
  runs: Run[];
  tagDefs: { tag: string; label: string }[];
  members: { name: string }[];
  sites: { id: number; name: string }[];
  sources: SourceOpt[];
  onBack: () => void;
  onToggleStatus: (w: Workflow) => void;
  /** Persists the draft; resolves to the saved workflow id (null on failure). */
  onSave: (state: EditorState) => Promise<number | null>;
  onRun: (workflowId: number, dry: boolean) => Promise<number | null>;
  onOpenRun: (n: number) => void;
}) {
  const [id, setId] = useState(initial.id);
  const [name, setName] = useState(initial.name);
  const [description, setDescription] = useState(initial.description);
  const [steps, setSteps] = useState(initial.steps);
  const [sel, setSel] = useState(0);
  const [dirty, setDirty] = useState(initial.id == null);
  const [insertAt, setInsertAt] = useState<number | null>(null);

  const wf = id != null ? workflows.find((w) => w.id === id) : undefined;

  // Esc closes the step picker (the drawer and menus close themselves)
  useEffect(() => {
    if (insertAt == null) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setInsertAt(null); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [insertAt]);

  const saveFlow = async (): Promise<number | null> => {
    const savedId = await onSave({ id, name, description, steps });
    if (savedId != null) { setId(savedId); setDirty(false); }
    return savedId;
  };

  const runFromEditor = async (dry: boolean) => {
    if (busy) return;
    let target = id;
    if (dirty || target == null) target = await saveFlow(); // honest: what runs is what's saved
    if (target != null) await onRun(target, dry);
  };

  const backToList = () => {
    if (dirty && !window.confirm("Discard unsaved changes to this flow?")) return;
    onBack();
  };

  const patchSteps = (fn: (steps: Step[]) => Step[]) => {
    setSteps((s) => fn(s));
    setDirty(true);
  };
  const setLabel = (i: number, v: string) => patchSteps((s) => s.map((x, j) => (j === i ? { ...x, label: v } : x)));
  const setConfig = (i: number, key: string, v: any) =>
    patchSteps((s) => s.map((x, j) => (j === i ? { ...x, config: { ...x.config, [key]: v } } : x)));
  const moveStep = (i: number, dir: -1 | 1) => {
    const j = i + dir;
    if (i === 0 || j === 0 || j < 0 || j >= steps.length) return;
    patchSteps((s) => { const n = [...s]; [n[i], n[j]] = [n[j], n[i]]; return n; });
    setSel(j);
  };
  const deleteStep = (i: number) => {
    if (i === 0) return;
    patchSteps((s) => s.filter((_, j) => j !== i));
    setSel((cur) => (cur >= i ? Math.max(0, cur - 1) : cur));
  };
  const insertStep = (at: number, kind: StepKind) => {
    const meta = KIND_META[kind];
    patchSteps((s) => {
      const n = [...s];
      n.splice(at, 0, { kind, label: meta.defLabel, config: { ...meta.defConfig } });
      return n;
    });
    setSel(at); setInsertAt(null);
  };

  const inserter = (at: number) => (
    <Fragment key={`ins-${at}`}>
      <div className={`fl-insert${insertAt === at ? " open" : ""}`}>
        <button className="fl-insert__dot" title="Add a step here" onClick={() => setInsertAt(insertAt === at ? null : at)}>
          <Ic.Plus size={12} />
        </button>
        <span className="fl-insert__hint">add a step</span>
      </div>
      {insertAt === at && <StepPicker onPick={(k) => insertStep(at, k)} onClose={() => setInsertAt(null)} />}
    </Fragment>
  );

  const stepCard = (s: Step, i: number) => {
    const sec = SECTION_OF[s.kind];
    const isBranch = i > 0 && !!s.config?.only_if_branch;
    const prevIsBranch = i > 0 && !!steps[i - 1]?.config?.only_if_branch;
    const cond = isBranch ? [...steps.slice(0, i)].reverse().find((x) => x.kind === "condition") : undefined;
    return (
      <Fragment key={`step-${i}`}>
        {isBranch && !prevIsBranch && cond && (
          <div className="fl-branchlabel">if {cond.config?.field ?? "value"} &gt; {cond.config?.greater_than ?? 0}</div>
        )}
        <div
          className={`card fl-step${sel === i ? " selected" : ""}${isBranch ? " branch" : ""}`}
          onClick={() => setSel(i)}
        >
          <div className="fl-step__top">
            <span className={`fl-chip ${SECTION_META[sec].cls}`}>{SECTION_META[sec].title}</span>
            <input
              className="fl-step__label"
              value={s.label}
              placeholder={KIND_META[s.kind].defLabel}
              onFocus={() => setSel(i)}
              onChange={(e) => setLabel(i, e.target.value)}
            />
            <span className="fl-step__tools">
              <button title="Move up" disabled={i <= 1} onClick={(e) => { e.stopPropagation(); moveStep(i, -1); }}>↑</button>
              <button title="Move down" disabled={i === 0 || i === steps.length - 1} onClick={(e) => { e.stopPropagation(); moveStep(i, 1); }}>↓</button>
              <button className="danger" title={i === 0 ? "Every flow needs its trigger" : "Delete step"} disabled={i === 0}
                onClick={(e) => { e.stopPropagation(); deleteStep(i); }}><Ic.Trash size={12} /></button>
            </span>
          </div>
          <div className="fl-step__sum">{stepIcon(s.kind)} {stepSummaryOf(s)}</div>
          {KIND_META[s.kind].llm && llmBadge}
        </div>
      </Fragment>
    );
  };

  // local alias so the map above stays terse
  const stepSummaryOf = (s: Step) => stepSummary(s, sites);

  const selStep = steps[Math.min(sel, steps.length - 1)];
  const selIdx = Math.min(sel, steps.length - 1);

  return (
    <>
      <div className="row fl-editor__bar">
        <button className="pagehead__back" onClick={backToList}><Ic.ChevL size={13} /> Flows</button>
        <span className="fl-crumb">/ {name || "Untitled flow"}</span>
        <span className="card__spacer" />
        {wf && (
          <span className="fl-editor__status">
            <button
              className={`fl-toggle${wf.status === "active" ? " on" : ""}`}
              title={wf.status === "active" ? "Active — click to pause" : "Paused — click to enable"}
              onClick={() => onToggleStatus(wf)}
            />
            {wf.status === "active" ? "Enabled" : "Paused"}
          </span>
        )}
        <Button disabled={busy} title="Executes transforms for real; side effects become previews" onClick={() => runFromEditor(true)}>
          <Ic.Eye size={13} /> Test run
        </Button>
        <Button variant="primary" disabled={busy} onClick={() => runFromEditor(false)}>
          <Ic.Play size={13} /> Run
        </Button>
      </div>

      <div className="card fl-editor__meta">
        <input className="fl-editor__name" value={name} placeholder="Name this flow"
          onChange={(e) => { setName(e.target.value); setDirty(true); }} />
        <input className="fl-editor__desc" value={description} placeholder="What does this flow guarantee? One sentence."
          onChange={(e) => { setDescription(e.target.value); setDirty(true); }} />
      </div>

      <div className="fl-editor">
        <div>
          <div className="fl-pipe">
            {steps.map((s, i) => (
              <Fragment key={i}>
                {i > 0 && inserter(i)}
                {stepCard(s, i)}
              </Fragment>
            ))}
            {inserter(steps.length)}
          </div>

          <div className="card fl-foot">
            <Button variant="primary" disabled={busy || !dirty} onClick={saveFlow}>
              <Ic.Check size={13} /> Save flow
            </Button>
            <Button disabled={busy} title="Executes transforms for real; side effects become previews — nothing is written" onClick={() => runFromEditor(true)}>
              <Ic.Eye size={13} /> {dirty ? "Save & test run" : "Test run"}
            </Button>
            <Button disabled={busy} onClick={() => runFromEditor(false)}>
              <Ic.Play size={13} /> {dirty ? "Save & run" : "Run"}
            </Button>
            <span className="card__spacer" />
            <span className={`fl-foot__hint${dirty ? " dirty" : ""}`}>
              {dirty ? "Unsaved changes — runs use the saved version, so Run saves first." : id == null ? "Not saved yet." : "Saved."}
            </span>
          </div>
        </div>

        {selStep && (
          <ConfigPanel
            key={`${selIdx}-${selStep.kind}`}
            step={selStep}
            onLabel={(v) => setLabel(selIdx, v)}
            onConfig={(k, v) => setConfig(selIdx, k, v)}
            tagDefs={tagDefs}
            members={members}
            sites={sites}
            sources={sources}
          />
        )}
      </div>

      <RunHistory runs={runs} wfId={id} onOpen={onOpenRun} />
    </>
  );
}
