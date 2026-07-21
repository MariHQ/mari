import { useMemo, useState } from "react";
import { Hatch } from "../components/art";
import * as Ic from "../components/icons";
import { SourceIcon } from "../components/shared";
import { SourceKey } from "../data/sources";
import {
  Button,
  Card,
  Chip,
  ChipStatus,
  EmptyState,
  Field,
  IconRing,
  Input,
  PageHeader,
  Stat,
  Spinner,
  StatusChip,
  Tabs,
  Textarea,
  fmtDate,
} from "../components/ui";
import { gql, useQuery } from "../lib/api";
import HarvestWizard from "./answers/HarvestWizard";
import "./answers.css";

type AnswerSource = { source: string; title: string };
type Answer = {
  id: number;
  question: string;
  answer: string;
  status: string; // draft | approved | retired
  owner: string;
  channels: string[];
  sources: AnswerSource[];
  served: number;
  spark: number[];
  updated: string;
};

type CoverageItem = { question: string; draft: string };

const CHANNELS = ["slack-bot", "support-widget", "docs-site"] as const;
const CHANNEL_LABEL: Record<string, string> = {
  "slack-bot": "Slack bot",
  "support-widget": "Support widget",
  "docs-site": "Docs site",
};

const FILTERS = ["All", "Approved", "Drafts", "Retired"] as const;
type Filter = (typeof FILTERS)[number];
const FILTER_STATUS: Record<Filter, string | null> = { All: null, Approved: "approved", Drafts: "draft", Retired: "retired" };

const ANSWERS_QUERY = `{ approvedAnswers { id question answer status owner channels sources served spark updated } }`;
const SESSIONS_QUERY = `{ chatSessions { id title messages { role content sources } } }`;

// coverage: recent user questions whose reply was NOT an approved answer
function mapCoverage(d: any): CoverageItem[] {
  const items: CoverageItem[] = [];
  const seen = new Set<string>();
  for (const s of d.chatSessions ?? []) {
    const msgs = s.messages ?? [];
    for (let i = 0; i < msgs.length - 1; i++) {
      if (msgs[i].role !== "user" || msgs[i + 1].role !== "assistant") continue;
      const reply = msgs[i + 1];
      if (reply.sources?.[0]?.source === "approved") continue; // already served verbatim
      const q = (msgs[i].content ?? "").trim();
      if (q.length < 8) continue; // skip "test"-style noise
      const key = q.toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      items.push({ question: q, draft: (reply.content ?? "").trim() });
    }
  }
  return items.slice(0, 5);
}

const SOURCE_KEYS: SourceKey[] = ["github", "slack", "gdocs", "notion", "granola", "docs"];
const asSourceKey = (s: string): SourceKey => (SOURCE_KEYS.includes(s as SourceKey) ? (s as SourceKey) : "docs");

const asChipStatus = (s: string): ChipStatus =>
  s === "approved" || s === "retired" ? s : "draft";

function initials(name: string) {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("");
}
function tintFor(name: string) {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) & 0xffff;
  return (h % 4) + 1;
}

/** Tiny hand-drawn sparkline from the served-per-day series. */
function Sparkline({ values, width = 92, height = 26 }: { values: number[]; width?: number; height?: number }) {
  if (!values.length) return null;
  const max = Math.max(...values, 1);
  const pad = 2.5;
  const pts = values.map((v, i) => {
    const x = pad + (i / Math.max(values.length - 1, 1)) * (width - pad * 2);
    // small deterministic wobble for the hand-drawn feel
    const wobble = ((i * 37) % 5 - 2) * 0.35;
    const y = height - pad - (v / max) * (height - pad * 2) + wobble;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const [lx, ly] = pts[pts.length - 1].split(",").map(Number);
  return (
    <svg className="ans-spark" width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden>
      <polyline points={pts.join(" ")} fill="none" stroke="var(--green, #2c6e49)" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={lx} cy={ly} r={2.2} fill="var(--green, #2c6e49)" />
    </svg>
  );
}

export default function AnswersPage() {
  const answersQ = useQuery<Answer[]>(ANSWERS_QUERY, { map: (d) => d.approvedAnswers ?? [] });
  const coverageQ = useQuery<CoverageItem[]>(SESSIONS_QUERY, { map: mapCoverage });
  const answers = useMemo(() => answersQ.data ?? [], [answersQ.data]);
  const refetch = () => {
    answersQ.refetch();
    coverageQ.refetch();
  };

  const [filter, setFilter] = useState<Filter>("All");
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState<Record<number, string>>({}); // id → action label

  // composer (new answer, possibly prefilled from coverage)
  const [composerOpen, setComposerOpen] = useState(false);
  const [draftQ, setDraftQ] = useState("");
  const [draftA, setDraftA] = useState("");
  const [saving, setSaving] = useState(false);

  // harvest wizard
  const [harvestOpen, setHarvestOpen] = useState(false);

  // inline per-card edit
  const [editId, setEditId] = useState<number | null>(null);
  const [editQ, setEditQ] = useState("");
  const [editA, setEditA] = useState("");

  const stats = useMemo(() => {
    const approved = answers.filter((a) => a.status === "approved").length;
    const drafts = answers.filter((a) => a.status === "draft").length;
    const served = answers.reduce((n, a) => n + (a.served || 0), 0);
    return { approved, drafts, served };
  }, [answers]);

  const visible = answers.filter((a) => {
    const want = FILTER_STATUS[filter];
    return want === null || a.status === want;
  });

  const openComposer = (q = "", a = "") => {
    setDraftQ(q);
    setDraftA(a);
    setComposerOpen(true);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const saveNew = async () => {
    if (!draftQ.trim() || !draftA.trim() || saving) return;
    setSaving(true);
    await gql(`mutation($q: String!, $a: String!) { upsertAnswer(question: $q, answer: $a) }`, {
      q: draftQ.trim(), a: draftA.trim(),
    });
    setSaving(false);
    setComposerOpen(false);
    setDraftQ("");
    setDraftA("");
    refetch();
  };

  const startEdit = (a: Answer) => {
    setEditId(a.id);
    setEditQ(a.question);
    setEditA(a.answer);
  };

  const saveEdit = async (id: number) => {
    if (!editQ.trim() || !editA.trim()) return;
    setBusy((b) => ({ ...b, [id]: "Saving…" }));
    await gql(`mutation($q: String!, $a: String!, $id: Int) { upsertAnswer(question: $q, answer: $a, id: $id) }`, {
      q: editQ.trim(), a: editA.trim(), id,
    });
    setBusy(({ [id]: _drop, ...rest }) => rest);
    setEditId(null);
    refetch();
  };

  const setStatus = async (a: Answer, status: string) => {
    setBusy((b) => ({ ...b, [a.id]: status === "approved" ? "Embedding…" : "Retiring…" }));
    await gql(`mutation($id: Int!, $s: String!) { setAnswerStatus(id: $id, status: $s) }`, { id: a.id, s: status });
    setBusy(({ [a.id]: _drop, ...rest }) => rest);
    refetch();
  };

  const toggleChannel = async (a: Answer, ch: string) => {
    if (a.status !== "approved") return;
    const next = a.channels.includes(ch) ? a.channels.filter((c) => c !== ch) : [...a.channels, ch];
    setBusy((b) => ({ ...b, [a.id]: "channels" }));
    await gql(`mutation($id: Int!, $ch: [String!]!) { setAnswerChannels(id: $id, channels: $ch) }`, { id: a.id, ch: next });
    setBusy(({ [a.id]: _drop, ...rest }) => rest);
    refetch();
  };

  const toggleExpand = (id: number) =>
    setExpanded((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  // Wizard finished importing: show the new drafts.
  const onHarvested = () => {
    setHarvestOpen(false);
    setFilter("Drafts");
    refetch();
  };

  return (
    <>
      <PageHeader
        eyebrow="Knowledge"
        title="Approved answers"
        description="Curated answers your bots and teams serve verbatim — no generation, no drift. The chat bot already serves these with an “Approved” badge."
        actions={
          <>
            <Button onClick={() => setHarvestOpen(true)}>
              <Ic.Sparkle size={15} /> Harvest questions
            </Button>
            <Button variant="primary" onClick={() => (composerOpen ? setComposerOpen(false) : openComposer())}>
              <Ic.Plus size={15} /> New answer
            </Button>
          </>
        }
      />

      <HarvestWizard open={harvestOpen} onOpenChange={setHarvestOpen} onImported={onHarvested} />

      {/* inline composer */}
      {composerOpen && (
        <Card title="New answer" className="ans-composer">
          <div className="ans-composer__grid">
            <Field label="Question">
              <Input
                placeholder="e.g. What is the uptime SLA?"
                value={draftQ} onChange={(e) => setDraftQ(e.target.value)}
                autoFocus
              />
            </Field>
            <Field label="Answer (served verbatim)">
              <Textarea
                placeholder="Write the exact answer bots and teammates should serve…"
                value={draftA} onChange={(e) => setDraftA(e.target.value)}
              />
            </Field>
          </div>
          <div className="ans-actionrow">
            <Button onClick={() => setComposerOpen(false)}>Cancel</Button>
            <Button variant="primary" disabled={saving || !draftQ.trim() || !draftA.trim()} onClick={saveNew}>
              <Ic.Quill size={14} /> {saving ? "Saving…" : "Save draft"}
            </Button>
          </div>
        </Card>
      )}

      {/* stat strip */}
      <div className="ans-stats">
        <Stat
          value={answersQ.data ? stats.approved : answersQ.loading ? <Spinner size="sm" /> : "—"}
          label="approved"
          sub="serving live"
          tone="green"
          swatch={<Hatch color="#2c6e49" />}
          ring={<IconRing tone="green"><Ic.CheckCircle size={17} /></IconRing>}
        />
        <Stat
          value={answersQ.data ? stats.drafts : answersQ.loading ? <Spinner size="sm" /> : "—"}
          label="drafts"
          sub="awaiting approval"
          tone="gold"
          swatch={<Hatch color="#a05e1c" />}
          ring={<IconRing tone="gold"><Ic.Pencil size={16} /></IconRing>}
        />
        <Stat
          value={answersQ.data ? stats.served.toLocaleString() : answersQ.loading ? <Spinner size="sm" /> : "—"}
          label="served this week"
          sub="verbatim, zero drift"
          tone="blue"
          swatch={<Hatch color="#35549d" />}
          ring={<IconRing tone="blue"><Ic.Send size={16} /></IconRing>}
        />
      </div>

      <div className="ans-layout">
        {/* main column */}
        <div>
          <Tabs
            variant="filter"
            ariaLabel="Filter answers by status"
            value={filter}
            onChange={setFilter}
            options={FILTERS.map((f) => ({ id: f, label: f }))}
          />

          {answersQ.loading && (
            <Card>
              <div style={{ display: "grid", placeItems: "center", minHeight: 120 }}>
                <Spinner size="sm" />
              </div>
            </Card>
          )}

          {answersQ.error && !answersQ.data && (
            <Card>
              <EmptyState icon={<Ic.Quill size={20} />}>API offline — answers unavailable.</EmptyState>
            </Card>
          )}

          {answersQ.data && visible.length === 0 && (
            <Card>
              <EmptyState
                icon={<Ic.Quill size={20} />}
                action={<Button compact onClick={() => openComposer()}><Ic.Plus size={13} /> New answer</Button>}
              >
                No {filter === "All" ? "" : filter.toLowerCase() + " "}answers yet.
              </EmptyState>
            </Card>
          )}

          {visible.map((a) => {
            const isEditing = editId === a.id;
            const action = busy[a.id];
            const long = a.answer.length > 190;
            const isOpen = expanded.has(a.id);
            return (
              <Card className="anscard" key={a.id}>
                {isEditing ? (
                  <div className="ans-editor">
                    <Input value={editQ} onChange={(e) => setEditQ(e.target.value)} aria-label="Question" />
                    <Textarea value={editA} onChange={(e) => setEditA(e.target.value)} aria-label="Answer" />
                    <div className="ans-actionrow">
                      <Button onClick={() => setEditId(null)}>Cancel</Button>
                      <Button variant="primary" disabled={!!action || !editQ.trim() || !editA.trim()} onClick={() => saveEdit(a.id)}>
                        {action === "Saving…" ? "Saving…" : "Save"}
                      </Button>
                    </div>
                  </div>
                ) : (
                  <div className="anscard__top">
                    <div className="anscard__main">
                      <h3 className="anscard__q">{a.question}</h3>
                      <p className={`anscard__body${long && !isOpen ? " clamped" : ""}`}>{a.answer}</p>
                      {long && (
                        <span className="anscard__more" role="button" onClick={() => toggleExpand(a.id)}>
                          {isOpen ? <>Show less <Ic.Chev size={12} /></> : <>Show more <Ic.ChevR size={12} /></>}
                        </span>
                      )}

                      <div className="anscard__meta">
                        <StatusChip status={asChipStatus(a.status)} />
                        <span className={`avatar avatar--sm avatar--tint${tintFor(a.owner)}`}>{initials(a.owner)}</span>
                        <span>{a.owner}</span>
                        <span className="updated">Updated {fmtDate(a.updated)}</span>
                      </div>

                      <div className="anscard__rows">
                        <div className="ansrow">
                          <span className="ansrow__label">Served to</span>
                          {CHANNELS.map((ch) => {
                            const on = a.channels.includes(ch);
                            return (
                              <Chip
                                key={ch}
                                tone={on ? "green" : "neutral"}
                                dot
                                onClick={() => toggleChannel(a, ch)}
                                disabled={a.status !== "approved" || action === "channels"}
                                title={a.status === "approved" ? (on ? `Stop serving to ${CHANNEL_LABEL[ch]}` : `Serve to ${CHANNEL_LABEL[ch]}`) : "Approve this answer to pick channels"}
                              >
                                {CHANNEL_LABEL[ch]}
                              </Chip>
                            );
                          })}
                          {a.status !== "approved" && <span className="ansrow__hint">approve to serve</span>}
                        </div>
                        {a.sources.length > 0 && (
                          <div className="ansrow">
                            <span className="ansrow__label">Sources</span>
                            {a.sources.map((s, i) => (
                              <Chip key={i} icon={<SourceIcon source={asSourceKey(s.source)} size={14} />}>
                                {s.title}
                              </Chip>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>

                    <div className="anscard__side">
                      {a.status === "approved" && (
                        <>
                          <span className="anscard__served"><b>{a.served.toLocaleString()}</b> served / wk</span>
                          <Sparkline values={a.spark} />
                        </>
                      )}
                      <div className="anscard__actions">
                        {a.status !== "approved" ? (
                          <Button compact variant="primary" disabled={!!action} onClick={() => setStatus(a, "approved")}>
                            <Ic.Check size={13} strokeWidth={2.2} /> {action === "Embedding…" ? "Embedding…" : "Approve"}
                          </Button>
                        ) : (
                          <Button compact disabled={!!action} onClick={() => setStatus(a, "retired")}>
                            {action === "Retiring…" ? "Retiring…" : "Retire"}
                          </Button>
                        )}
                        <Button compact disabled={!!action} onClick={() => startEdit(a)}>
                          <Ic.Pencil size={13} /> Edit
                        </Button>
                      </div>
                    </div>
                  </div>
                )}
              </Card>
            );
          })}
        </div>

        {/* right rail */}
        <div className="ans-rail">
          <Card title="Coverage" icon={<IconRing><Ic.Search size={15} /></IconRing>}>
            <p className="ans-note ans-note--first">Recent questions the bot had to generate an answer for — turn them into approved answers.</p>
            {coverageQ.loading && (
              <div style={{ display: "grid", placeItems: "center", minHeight: 60 }}>
                <Spinner size="sm" />
              </div>
            )}
            {coverageQ.error && !coverageQ.data && (
              <EmptyState>API offline — coverage unavailable.</EmptyState>
            )}
            {coverageQ.data && coverageQ.data.length === 0 && (
              <EmptyState>No uncovered questions yet.</EmptyState>
            )}
            {(coverageQ.data ?? []).map((c) => (
              <div className="covitem" key={c.question}>
                <p className="covitem__q">{c.question}</p>
                <div className="covitem__foot">
                  <span className="covitem__asks">from chat logs</span>
                  <Button compact onClick={() => openComposer(c.question, c.draft)}>
                    <Ic.Quill size={13} /> Draft answer
                  </Button>
                </div>
              </div>
            ))}
          </Card>

          <Card title="How serving works" icon={<IconRing tone="green"><Ic.ShieldCheck size={15} /></IconRing>}>
            <p className="ans-note ans-note--first">
              Approving an answer <b>embeds it</b> so incoming questions match semantically. The Slack bot
              and support widget then serve the text <b>verbatim</b> with an <b>Approved</b> badge — nothing
              is regenerated, so the answer never drifts.
            </p>
          </Card>
        </div>
      </div>
    </>
  );
}
