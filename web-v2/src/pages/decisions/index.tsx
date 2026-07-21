import { useMemo, useState } from "react";
import * as Ic from "../../components/icons";
import { SourceIcon } from "../../components/shared";
import {
  Button,
  Card,
  EmptyState,
  Field,
  Input,
  PageHeader,
  Spinner,
  Tabs,
  Textarea,
  useToast,
} from "../../components/ui";
import { gql, useQuery } from "../../lib/api";
import { DecisionCard } from "./DecisionCard";
import { DECISIONS_QUERY, Decision, FILTERS, Filter, ImpactState, sourceKind } from "./data";
import "../decisions.css";

export default function DecisionsPage() {
  const decisionsQ = useQuery<Decision[]>(DECISIONS_QUERY, { map: (d) => d.decisions ?? [] });
  const decisions = useMemo(() => decisionsQ.data ?? [], [decisionsQ.data]);
  const { refetch } = decisionsQ;
  const toast = useToast();

  const [filter, setFilter] = useState<Filter>("All");

  // composer
  const [composerOpen, setComposerOpen] = useState(false);
  const [newStatement, setNewStatement] = useState("");
  const [newContext, setNewContext] = useState("");
  const [newSource, setNewSource] = useState("");
  const [saving, setSaving] = useState(false);

  // per-decision working state
  const [impact, setImpact] = useState<Record<number, ImpactState>>({});
  const [ratifying, setRatifying] = useState<number | null>(null);
  const [justStamped, setJustStamped] = useState<number | null>(null);
  const [supersedeFor, setSupersedeFor] = useState<number | null>(null);
  const [supersedeText, setSupersedeText] = useState("");
  const [superseding, setSuperseding] = useState(false);

  // LLM scan over recent documents
  const [scanning, setScanning] = useState(false);

  const scanForDecisions = async () => {
    if (scanning) return;
    setScanning(true);
    const d: any = await gql(`mutation { scanDecisions }`);
    setScanning(false);
    if (d == null || typeof d.scanDecisions !== "number") {
      toast("Scan failed — couldn’t reach Mari. Is the API running?", "error");
      return;
    }
    const n = d.scanDecisions;
    toast(
      n > 0 ? `${n} new decision${n === 1 ? "" : "s"} proposed — review in Awaiting sign-off` : "Nothing new found",
      "success",
    );
    refetch();
  };

  const capture = async () => {
    if (!newStatement.trim() || saving) return;
    setSaving(true);
    await gql(
      `mutation($statement: String!, $context: String!, $sourceLabel: String!) {
        addDecision(statement: $statement, context: $context, sourceLabel: $sourceLabel)
      }`,
      {
        statement: newStatement.trim(),
        context: newContext.trim(),
        sourceLabel: newSource.trim(),
      },
    );
    setSaving(false);
    setNewStatement("");
    setNewContext("");
    setNewSource("");
    setComposerOpen(false);
    refetch();
  };

  const ratify = async (id: number) => {
    if (ratifying !== null) return;
    setRatifying(id);
    await gql(`mutation($id: Int!) { ratifyDecision(id: $id) }`, { id });
    setRatifying(null);
    setJustStamped(id);
    refetch();
  };

  const supersede = async (id: number) => {
    if (!supersedeText.trim() || superseding) return;
    setSuperseding(true);
    await gql(`mutation($id: Int!, $byStatement: String!) { supersedeDecision(id: $id, byStatement: $byStatement) }`, {
      id,
      byStatement: supersedeText.trim(),
    });
    setSuperseding(false);
    setSupersedeFor(null);
    setSupersedeText("");
    refetch();
  };

  const runImpact = async (d: Decision) => {
    const existing = impact[d.id];
    if (existing?.loading) return;
    if (existing?.docs) {
      setImpact((m) => ({ ...m, [d.id]: { ...existing, open: !existing.open } }));
      return;
    }
    setImpact((m) => ({ ...m, [d.id]: { loading: true, open: true } }));
    const r: any = await gql(
      `mutation($id: Int!) { decisionImpact(id: $id) { claim summary docs { title source severity reason } } }`,
      { id: d.id },
    );
    const res = r?.decisionImpact;
    setImpact((m) => ({
      ...m,
      [d.id]: res
        ? { loading: false, open: true, summary: res.summary, docs: res.docs ?? [] }
        : { loading: false, open: true, summary: "Impact analysis unavailable — is the API running?", docs: [] },
    }));
    if (res) refetch(); // decisionImpact persists impactSummary/impactCount
  };

  const setImpactOpen = (id: number, open: boolean) =>
    setImpact((m) => (m[id] ? { ...m, [id]: { ...m[id], open } } : m));

  const createTasks = async (d: Decision) => {
    const im = impact[d.id];
    const docs = im?.docs ?? [];
    if (!docs.length || im?.creatingTasks || im?.tasksCreated) return;
    setImpact((m) => ({ ...m, [d.id]: { ...m[d.id], creatingTasks: true } }));
    for (const doc of docs) {
      await gql(
        `mutation($title: String!, $kind: String!, $kindLabel: String!) { createTask(title: $title, kind: $kind, kindLabel: $kindLabel) }`,
        { title: `Update: ${doc.title}`, kind: "decision", kindLabel: "Decision impact" },
      );
    }
    setImpact((m) => ({ ...m, [d.id]: { ...m[d.id], creatingTasks: false, tasksCreated: docs.length } }));
  };

  const counts = useMemo(() => {
    const c: Record<Filter, number> = { All: decisions.length, Proposed: 0, Ratified: 0, Superseded: 0 };
    for (const d of decisions) {
      if (d.status === "proposed") c.Proposed++;
      else if (d.status === "ratified") c.Ratified++;
      else c.Superseded++;
    }
    return c;
  }, [decisions]);

  const visible = decisions.filter((d) => filter === "All" || d.status === filter.toLowerCase());
  const awaiting = decisions.filter((d) => d.status === "proposed");

  return (
    <>
      <PageHeader
        eyebrow="Ledger"
        title="Decisions"
        description="One decision, one record, ratified by the people accountable."
        actions={
          <>
            <Button onClick={scanForDecisions} disabled={scanning}>
              <Ic.Sparkle size={15} />
              {scanning ? "Mari is reading recent documents…" : "Scan for decisions"}
            </Button>
            <Button variant="primary" onClick={() => setComposerOpen((v) => !v)}>
              <Ic.Plus size={15} /> Capture decision
            </Button>
          </>
        }
      />

      {composerOpen && (
        <Card
          className="dec-composer"
          title="Capture a decision"
          hint="Write it the way you would defend it later — one sentence, no hedging."
        >
          <div className="dec-composer__grid">
            <Field label="Statement">
              <Input
                placeholder={"e.g. Free tier ends September 1"}
                value={newStatement}
                onChange={(e) => setNewStatement(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && capture()}
                autoFocus
              />
            </Field>
            <Field label="Context" hint="Why it was decided, what it replaces, open questions.">
              <Textarea
                short
                placeholder="Security review outcome; support macros still describe the old behavior…"
                value={newContext}
                onChange={(e) => setNewContext(e.target.value)}
              />
            </Field>
            <Field label="Source" hint="Where the thread lives.">
              <Input
                placeholder={"Slack #product · May 13"}
                value={newSource}
                onChange={(e) => setNewSource(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && capture()}
              />
            </Field>
          </div>
          <div className="dec-composer__foot">
            <Button onClick={() => setComposerOpen(false)}>Cancel</Button>
            <Button variant="primary" onClick={capture} disabled={saving || !newStatement.trim()}>
              <Ic.Quill size={14} /> {saving ? "Capturing…" : "Capture decision"}
            </Button>
          </div>
        </Card>
      )}

      <div className="dec-layout">
        {/* ————— main ledger column ————— */}
        <div className="dec-list">
          {decisionsQ.loading && (
            <Card>
              <div style={{ display: "grid", placeItems: "center", minHeight: 120 }}>
                <Spinner size="sm" label="Loading decisions" />
              </div>
            </Card>
          )}
          {decisionsQ.error && !decisionsQ.data && (
            <Card>
              <EmptyState icon={<Ic.Quill size={22} />}>API offline — the ledger is unavailable.</EmptyState>
            </Card>
          )}
          {decisionsQ.data && visible.length === 0 && (
            <Card>
              <EmptyState icon={<Ic.Quill size={22} />}>
                No {filter.toLowerCase()} decisions in the ledger yet.
              </EmptyState>
            </Card>
          )}
          {visible.map((d) => (
            <DecisionCard
              key={d.id}
              d={d}
              im={impact[d.id]}
              ratifying={ratifying}
              justStamped={justStamped}
              showSupersedeForm={supersedeFor === d.id}
              supersedeText={supersedeText}
              superseding={superseding}
              onRatify={ratify}
              onOpenSupersede={(id) => {
                setSupersedeFor(id);
                setSupersedeText("");
              }}
              onCancelSupersede={() => setSupersedeFor(null)}
              onSupersedeTextChange={setSupersedeText}
              onSupersede={supersede}
              onRunImpact={runImpact}
              onSetImpactOpen={setImpactOpen}
              onCreateTasks={createTasks}
            />
          ))}
        </div>

        {/* ————— right rail ————— */}
        <aside className="dec-rail">
          <Card className="dec-rail__card" title={<><Ic.Quill size={16} /> Awaiting sign-off</>}>
            {decisionsQ.data && awaiting.length === 0 && <p className="dec-await__empty">Nothing waiting — every decision is signed off.</p>}
            {!decisionsQ.data && <p className="dec-await__empty">{decisionsQ.loading ? "Loading…" : "API offline."}</p>}
            {awaiting.map((d) => (
              <div key={d.id} className="dec-await">
                <div className="dec-await__row">
                  <span className="dec-await__stmt">
                    {d.sourceLabel.startsWith("Mari scan") && (
                      <span className="dec-spark" title="Found by Mari scan" aria-label="Found by Mari scan">✨</span>
                    )}
                    {`“${d.statement}”`}
                  </span>
                </div>
                <div className="dec-await__meta">
                  {d.sourceLabel && (
                    <>
                      <SourceIcon source={sourceKind(d.sourceLabel)} size={13} />
                      <span>{d.sourceLabel}</span>
                    </>
                  )}
                  <Button
                    compact
                    className="dec-await__ratify"
                    onClick={() => ratify(d.id)}
                    disabled={ratifying !== null}
                  >
                    <Ic.Check size={12} /> {ratifying === d.id ? "…" : "Ratify"}
                  </Button>
                </div>
              </div>
            ))}
          </Card>

          <Card className="dec-rail__card" title={<><Ic.Book size={16} /> How this works</>}>
            <ul className="dec-note">
              <li>
                <Ic.Chat size={14} />
                <span>Decisions are <b>captured from Slack</b> or written here — one sentence, one record.</span>
              </li>
              <li>
                <Ic.Check size={14} />
                <span><b>Ratification is the sign-off</b> — the stamp of the people accountable.</span>
              </li>
              <li>
                <Ic.LineageIcon size={14} />
                <span><b>Impact runs after ratification</b>, citing evidence for every document it flags.</span>
              </li>
            </ul>
          </Card>

          <Card className="dec-rail__card" title={<><Ic.Eye size={16} /> Filter the ledger</>}>
            <Tabs
              variant="filter"
              ariaLabel="Filter the ledger"
              value={filter}
              onChange={setFilter}
              options={FILTERS.map((f) => ({ id: f, label: f, count: counts[f] }))}
            />
          </Card>
        </aside>
      </div>
    </>
  );
}
