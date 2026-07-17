// Insights — proof the knowledge cloud is working: searches, answers,
// drift caught, docs fixed; plus the two differentiators — deterministic
// LLM-readability scoring and the glossary harvest.

import { useState } from "react";
import { Hatch } from "../components/art";
import * as Ic from "../components/icons";
import { SourceIcon } from "../components/shared";
import {
  Button,
  Card,
  Chip,
  ChipTone,
  EmptyState,
  IconRing,
  IconRingTone,
  PageHeader,
  Spinner,
  Stat,
  fmtDate,
} from "../components/ui";
import { SourceKey } from "../data/sources";
import { gql, useQuery } from "../lib/api";
import "./insights.css";

/** Calm, centered loading placeholder — reserves height so layout doesn't jump. */
function Loading({ minHeight = 90 }: { minHeight?: number }) {
  return (
    <div style={{ display: "grid", placeItems: "center", minHeight }}>
      <Spinner size="sm" />
    </div>
  );
}

type InsightStats = { searches: number; answersServed: number; driftCaught: number; docsFixed: number; since: string };
type Freshness = { source: string; fresh: number; aging: number; stale: number };
type ReadabilityRow = { id: number; title: string; source: string; grade: string; note: string };
type GlossaryCandidate = { id: number; term: string; variants: string; definition: string };
type AuditRow = { id: number; actor: string; verb: string; target: string; at: string };

const STATS_QUERY = `{ insightStats { searches answersServed driftCaught docsFixed since } }`;
const FRESHNESS_QUERY = `{ freshness { source fresh aging stale } }`;
const READABILITY_QUERY = `{ readability { id title source grade note } }`;
const GLOSSARY_QUERY = `{ glossaryCandidates { id term variants definition } }`;
const AUDIT_QUERY = `{ auditLog(limit: 10) { id actor verb target at } }`;

const STAT_CARDS: { key: Exclude<keyof InsightStats, "since">; label: string; color: string; tone: IconRingTone; icon: React.ReactNode }[] = [
  { key: "searches", label: "Searches", color: "#5c7a4c", tone: "green", icon: <Ic.Search size={17} /> },
  { key: "answersServed", label: "Answers served", color: "#35549d", tone: "blue", icon: <Ic.Chat size={17} /> },
  { key: "driftCaught", label: "Drift caught", color: "#bf4f2e", tone: "red", icon: <Ic.ShieldCheck size={17} /> },
  { key: "docsFixed", label: "Docs fixed", color: "#c8973a", tone: "gold", icon: <Ic.Pencil size={17} /> },
];

const FRESH_COLORS = { fresh: "#5c7a4c", aging: "#c8973a", stale: "#bf4f2e" } as const;
const SOURCE_NAMES: Record<string, string> = { github: "GitHub", slack: "Slack", gdocs: "Drive", notion: "Notion", granola: "Granola", docs: "Docs" };

/** One pencil-drawn stacked bar: hand-jittered rect heights, wobble filter. */
function FreshBar({ row }: { row: Freshness }) {
  const total = row.fresh + row.aging + row.stale;
  const W = 300;
  const segs: { key: string; color: string; x: number; w: number }[] = [];
  let x = 2;
  (["fresh", "aging", "stale"] as const).forEach((k) => {
    const v = row[k];
    if (v <= 0 || total === 0) return;
    const w = (v / total) * (W - 4);
    segs.push({ key: k, color: FRESH_COLORS[k], x, w });
    x += w;
  });
  return (
    <svg className="fresh-row__bar" viewBox={`0 0 ${W} 24`} preserveAspectRatio="none" aria-hidden>
      {total === 0 ? (
        <line x1="2" y1="12" x2={W - 2} y2="12" stroke="var(--line)" strokeWidth="1.6" strokeDasharray="4 6" />
      ) : (
        <g filter="url(#sketch-soft)">
          {segs.map((s, i) => {
            // slightly irregular heights so the chart reads as pencil, not pixels
            const y = 3.5 + ((i * 7 + Math.round(s.w)) % 3) * 0.9;
            const h = 17 - ((i * 5 + Math.round(s.w)) % 3) * 1.2;
            return (
              <g key={s.key}>
                <rect x={s.x} y={y} width={Math.max(s.w - 1.5, 1.5)} height={h} rx="2.5" fill={s.color} opacity="0.78" />
                <rect x={s.x} y={y} width={Math.max(s.w - 1.5, 1.5)} height={h} rx="2.5" fill="none" stroke={s.color} strokeWidth="1.2" opacity="0.65" />
              </g>
            );
          })}
        </g>
      )}
    </svg>
  );
}

function LegendSwatch({ color }: { color: string }) {
  return (
    <svg width="15" height="13" viewBox="0 0 15 13" aria-hidden>
      <g filter="url(#sketch-soft)">
        <rect x="1" y="1" width="13" height="11" rx="2" fill={color} opacity="0.75" />
        <rect x="1" y="1" width="13" height="11" rx="2" fill="none" stroke={color} strokeWidth="1" />
      </g>
    </svg>
  );
}

const GRADE_TONES: Record<string, ChipTone> = { A: "green", B: "gold", C: "red" };

function GradeChip({ grade }: { grade: string }) {
  const g = grade.trim().toUpperCase();
  const tone = GRADE_TONES[g];
  if (!tone) return <Chip tone="faint" caps>—</Chip>;
  return <Chip tone={tone} caps>{g}</Chip>;
}

const asSourceKey = (s: string): SourceKey =>
  (["github", "slack", "gdocs", "notion", "granola", "docs"].includes(s) ? s : "docs") as SourceKey;

const fmt = (n: number) => n.toLocaleString("en-US");

export default function InsightsPage() {
  const [scoring, setScoring] = useState(false);
  const [harvesting, setHarvesting] = useState(false);
  const [hidden, setHidden] = useState<number[]>([]); // locally-resolved glossary rows

  const statsQ = useQuery<InsightStats>(STATS_QUERY, { map: (d) => d.insightStats });
  const freshnessQ = useQuery<Freshness[]>(FRESHNESS_QUERY, { map: (d) => d.freshness ?? [] });
  const readabilityQ = useQuery<ReadabilityRow[]>(READABILITY_QUERY, { map: (d) => d.readability ?? [] });
  const glossaryQ = useQuery<GlossaryCandidate[]>(GLOSSARY_QUERY, { map: (d) => d.glossaryCandidates ?? [] });
  const auditQ = useQuery<AuditRow[]>(AUDIT_QUERY, { map: (d) => d.auditLog ?? [] });

  const candidates = (glossaryQ.data ?? []).filter((c) => !hidden.includes(c.id));
  const auditRows = (auditQ.data ?? []).filter((a) => !a.target.startsWith("__")).slice(0, 8);

  const scoreDocs = async () => {
    setScoring(true);
    try {
      await gql(`mutation { scoreReadability }`);
    } finally {
      setScoring(false);
      readabilityQ.refetch();
      auditQ.refetch();
    }
  };

  const harvest = async () => {
    setHarvesting(true);
    try {
      await gql(`mutation { harvestGlossary }`);
    } finally {
      setHarvesting(false);
      glossaryQ.refetch();
      auditQ.refetch();
    }
  };

  const resolve = async (c: GlossaryCandidate, accept: boolean) => {
    setHidden((h) => [...h, c.id]);
    if (c.id > 0) {
      await gql(`mutation($id: Int!, $accept: Boolean!) { promoteGlossaryCandidate(id: $id, accept: $accept) }`, { id: c.id, accept });
      glossaryQ.refetch();
      auditQ.refetch();
    }
  };

  return (
    <>
      <PageHeader
        title="Insights"
        description={statsQ.data?.since
          ? `How your knowledge is holding up — counting since ${fmtDate(statsQ.data.since)}.`
          : "How your knowledge is holding up."}
      />

      {/* stat row */}
      <div className="ins-statrow">
        {STAT_CARDS.map((s) => (
          <Stat
            key={s.key}
            value={statsQ.data ? fmt(statsQ.data[s.key]) : statsQ.loading ? <Spinner size="sm" /> : "—"}
            label={s.label}
            swatch={<Hatch color={s.color} />}
            ring={<IconRing tone={s.tone}>{s.icon}</IconRing>}
          />
        ))}
      </div>

      <div className="ins-grid">
        {/* freshness by source */}
        <Card
          title="Freshness by source"
          icon={<IconRing><Ic.Leaf size={16} /></IconRing>}
          hint={freshnessQ.data ? `${freshnessQ.data.reduce((n, r) => n + r.fresh + r.aging + r.stale, 0)} docs` : undefined}
        >
          {freshnessQ.loading ? (
            <Loading minHeight={140} />
          ) : !freshnessQ.data ? (
            <EmptyState>API offline — freshness unavailable.</EmptyState>
          ) : (
            <>
              <div className="fresh-list">
                {freshnessQ.data.map((row) => (
                  <div className="fresh-row" key={row.source}>
                    <span className="fresh-row__src">
                      <SourceIcon source={asSourceKey(row.source)} size={19} />
                      <span className="fresh-row__name">{SOURCE_NAMES[row.source] ?? row.source}</span>
                    </span>
                    <FreshBar row={row} />
                    <span className="fresh-row__count">{row.fresh + row.aging + row.stale} docs</span>
                  </div>
                ))}
              </div>
              <div className="fresh-legend">
                <div><LegendSwatch color={FRESH_COLORS.fresh} /> Fresh</div>
                <div><LegendSwatch color={FRESH_COLORS.aging} /> Aging</div>
                <div><LegendSwatch color={FRESH_COLORS.stale} /> Stale</div>
              </div>
            </>
          )}
        </Card>

        {/* LLM readability */}
        <Card
          title="LLM readability"
          icon={<IconRing><Ic.Eye size={16} /></IconRing>}
          actions={(
            <Button compact onClick={() => void scoreDocs()} disabled={scoring}>
              <Ic.Sparkle size={14} /> {scoring ? "Scoring…" : "Score docs"}
            </Button>
          )}
        >
          {readabilityQ.loading ? (
            <Loading minHeight={140} />
          ) : !readabilityQ.data ? (
            <EmptyState>API offline — readability scores unavailable.</EmptyState>
          ) : (
            <table className="mtable">
              <thead>
                <tr>
                  <th>Document</th>
                  <th>Source</th>
                  <th>Grade</th>
                  <th>Note</th>
                </tr>
              </thead>
              <tbody>
                {readabilityQ.data.map((r) => (
                  <tr key={r.id}>
                    <td>{r.title}</td>
                    <td><SourceIcon source={asSourceKey(r.source)} size={18} /></td>
                    <td><GradeChip grade={r.grade} /></td>
                    <td><span className="ins-note">{r.note || "—"}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <div className="ins-explain">Deterministic scoring — sentence length and vocabulary, the same result every run.</div>
        </Card>

        {/* glossary health */}
        <Card
          title="Glossary health"
          icon={<IconRing><Ic.Book size={16} /></IconRing>}
          actions={(
            <Button compact onClick={() => void harvest()} disabled={harvesting}>
              <Ic.Refresh size={14} /> {harvesting ? "Harvesting…" : "Harvest terms"}
            </Button>
          )}
        >
          {harvesting && (
            <div className="gloss-running"><Spinner size="sm" label="Harvesting terms" /> Mari is reading the corpus…</div>
          )}
          {glossaryQ.loading ? (
            <Loading />
          ) : !glossaryQ.data ? (
            <EmptyState>API offline — glossary unavailable.</EmptyState>
          ) : (
          <div className="gloss-list">
            {candidates.map((c) => (
              <div className="gloss-row" key={c.id}>
                <div className="gloss-row__body">
                  <span className="gloss-row__term">{c.term}</span>
                  <span className="gloss-row__variants">{c.variants}</span>
                  <div className="gloss-row__def">{c.definition}</div>
                </div>
                <div className="gloss-row__acts">
                  <Button compact variant="success" onClick={() => void resolve(c, true)}>
                    <Ic.Check size={13} strokeWidth={2.4} /> Accept
                  </Button>
                  <Button compact onClick={() => void resolve(c, false)}>Dismiss</Button>
                </div>
              </div>
            ))}
            {candidates.length === 0 && !harvesting && (
              <EmptyState>No inconsistencies pending — harvest to re-check.</EmptyState>
            )}
          </div>
          )}
        </Card>

        {/* recent audit activity */}
        <Card
          title="Recent audit activity"
          icon={<IconRing><Ic.Clock size={16} /></IconRing>}
          hint={auditQ.data ? `Last ${auditRows.length} events` : undefined}
        >
          {auditQ.loading ? (
            <Loading />
          ) : !auditQ.data ? (
            <EmptyState>API offline — audit log unavailable.</EmptyState>
          ) : (
            <div className="audit-list">
              {auditRows.map((a) => (
                <div className="audit-row" key={a.id}>
                  <span className="audit-row__actor">{a.actor}</span>
                  <span className="audit-row__verb">{a.verb}</span>
                  <span className="audit-row__target">{a.target}</span>
                  {/* audit `at` arrives pre-formatted from the API ("May 11, 4:12 PM") */}
                  <span className="audit-row__at">{a.at}</span>
                </div>
              ))}
              {auditRows.length === 0 && (
                <EmptyState>Nothing logged yet.</EmptyState>
              )}
            </div>
          )}
        </Card>
      </div>
    </>
  );
}
