import { Fragment, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Bars, Hatch } from "../components/art";
import * as Ic from "../components/icons";
import { Pill, SourceIcon } from "../components/shared";
import { Avatar, EmptyState, IconRing, Spinner, Stat, fmtDate, fmtDateTime, type IconRingTone, type StatTone } from "../components/ui";
import { SourceKey } from "../data/sources";
import { gql, useQuery } from "../lib/api";
import { useAuth } from "../lib/auth";
import { KIND_ICON, SECTION_OF, fmtStarted, normStatus, type StepKind } from "./flows/data";
import DigestCard from "./overview/DigestCard";
import "./overview.css";

type Task = { id?: number; text: string; who: string; tint: number; pill: string; pillText: string; done?: boolean };
type PulseTile = { key: SourceKey; name: string; stat: string; unit: string; status: string; bars: number[] };
type FeedItem = { id: number; kind: string; actor: string; text: string; target: string; at: string };
type RecentDoc = { id: number; source: string; title: string; date: string };
type OverviewStats = { changes: number; factsReview: number; flowsRunning: number; flowsActive: number };
type StatCard = { num: number; label: string; sub: string; tone: string; icon: string };
type WfStrip = { id: number; name: string; status: string; nodes: { kind: string; label: string }[] };
type WfRun = { id: number; workflowId: number; status: string; started: string };

const TASKS_QUERY = `{ tasks { id title assigneeInitials assigneeTint kind kindLabel done } }`;
const OVERVIEW_QUERY = `{ overviewStats }`;
const PULSE_QUERY = `{ sourcePulse { provider name status stat unit bars docsCount health } }`;
const FEED_QUERY = `{ activityFeed(limit: 12) { id kind actor text target at } }`;
const RECENT_QUERY = `{ search(query: "") { id source title date } }`;

const STAT_COLORS: Record<string, string> = { green: "var(--green)", red: "var(--red)", blue: "var(--blue)" };
const STAT_ICONS: Record<string, React.ReactNode> = {
  clipboard: <Ic.Clipboard size={17} />,
  check: <Ic.CheckCircle size={17} />,
  play: <Ic.Play size={17} />,
};
// seed stats: "changes" (documents) → knowledge, "facts to review" → facts, "flows running" → flows
const STAT_LINKS: Record<string, string> = { clipboard: "/knowledge", check: "/facts", play: "/flows" };

const FEED_ICONS: Record<string, React.ReactNode> = {
  run: <Ic.Flow size={14} />,
  deploy: <Ic.Send size={14} />,
  fact: <Ic.ShieldCheck size={14} />,
  task: <Ic.Clipboard size={14} />,
  sync: <Ic.Refresh size={14} />,
  link: <Ic.LineageIcon size={14} />,
  edit: <Ic.Pencil size={14} />,
};

// keep the live feed focused on work: runs, edits, deploys — never slack chatter
const isNoise = (e: FeedItem) => /slack/i.test(`${e.kind} ${e.text}`);

// stat-card chrome while the API answers — labels only, never canned numbers
const STAT_PLACEHOLDERS: Pick<StatCard, "label" | "tone" | "icon">[] = [
  { label: "changes", tone: "green", icon: "clipboard" },
  { label: "facts to review", tone: "red", icon: "check" },
  { label: "flows running", tone: "blue", icon: "play" },
];

/** Calm, centered loading placeholder — reserves height so layout doesn't jump. */
function Loading({ minHeight = 90 }: { minHeight?: number }) {
  return (
    <div style={{ display: "grid", placeItems: "center", minHeight }}>
      <Spinner size="sm" />
    </div>
  );
}

function greetingWord() {
  const h = new Date().getHours();
  return h < 12 ? "morning" : h < 18 ? "afternoon" : "evening";
}

export default function Overview() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [tasks, setTasks] = useState<Task[] | null>(null); // null → still loading
  const [tasksError, setTasksError] = useState(false);

  // stat cards — live counts from the API
  const statsQ = useQuery<StatCard[]>(OVERVIEW_QUERY, {
    map: (d) => {
      const o = d.overviewStats as OverviewStats | undefined;
      if (!o) return [];
      return [
        { num: o.changes, label: "changes", sub: "this week", tone: "green", icon: "clipboard" },
        { num: o.factsReview, label: "facts to review", sub: o.factsReview > 0 ? "awaiting verification" : "all verified", tone: "red", icon: "check" },
        { num: o.flowsRunning, label: "flows running", sub: o.flowsRunning > 0 ? "All healthy" : "Idle", tone: "blue", icon: "play" },
      ];
    },
  });
  const statCards = statsQ.data?.length ? statsQ.data : null;

  const pulseQ = useQuery<PulseTile[]>(PULSE_QUERY, {
    map: (d) =>
      (d.sourcePulse ?? []).map((s: any) => ({
        key: s.provider as SourceKey, name: s.name, stat: s.stat, unit: s.unit, status: s.status, bars: s.bars,
      })),
  });

  // live activity — refreshed every 10s
  const feedQ = useQuery<FeedItem[]>(FEED_QUERY, { map: (d) => d.activityFeed ?? [] });
  const { refetch: refetchFeed } = feedQ;
  useEffect(() => {
    const t = setInterval(() => refetchFeed(), 10_000);
    return () => clearInterval(t);
  }, [refetchFeed]);
  const feedRows = (feedQ.data ?? []).filter((e) => !isNoise(e)).slice(0, 8);

  const recentQ = useQuery<RecentDoc[]>(RECENT_QUERY, { map: (d) => (d.search ?? []).slice(0, 3) });
  const recentDocs = recentQ.data ?? [];

  // workflow strip — real flows + their latest run (no seed exhibit content)
  const wfQ = useQuery<WfStrip[]>(`{ workflows { id name status nodes } }`, { map: (d) => d.workflows ?? [] });
  const wfRunsQ = useQuery<WfRun[]>(`{ workflowRuns { id workflowId status started } }`, { map: (d) => d.workflowRuns ?? [] });
  const stripWf = (wfQ.data ?? []).find((w) => w.status === "active") ?? (wfQ.data ?? [])[0] ?? null;
  const stripRun = stripWf
    ? (wfRunsQ.data ?? []).filter((r) => r.workflowId === stripWf.id).sort((a, b) => b.id - a.id)[0] ?? null
    : null;

  const loadTasks = () =>
    gql(TASKS_QUERY).then((d: any) => {
      if (d?.tasks) {
        setTasksError(false);
        setTasks(
          d.tasks.map((t: any) => ({
            id: t.id, text: t.title, who: t.assigneeInitials, tint: t.assigneeTint,
            pill: t.kind, pillText: t.kindLabel, done: t.done,
          }))
        );
      } else {
        setTasksError(true);
      }
    });

  useEffect(() => { loadTasks(); }, []);

  const visibleTasks = (tasks ?? []).slice(0, 4);
  const anyDone = (tasks ?? []).some((t) => t.done);

  const toggle = (t: Task) => {
    const done = !t.done;
    setTasks((ts) => ts && ts.map((x) => (x === t ? { ...x, done } : x)));
    if (t.id != null) gql(`mutation($id: Int!, $done: Boolean!) { setTaskDone(id: $id, done: $done) }`, { id: t.id, done });
  };

  const clearDone = async () => {
    setTasks((ts) => ts && ts.filter((t) => !t.done));
    await gql(`mutation { clearDoneTasks }`);
    loadTasks();
  };

  return (
    <>
      {/* row 1: greeting + stat cards */}
      <div className="ov-row1">
        <div className="greeting">
          <h2>
            Good {greetingWord()},<br />{(user?.name ?? "").split(" ")[0] || "there"}
          </h2>
          <span className="greeting__underline" />
        </div>

        {(statCards ?? STAT_PLACEHOLDERS.map((p) => ({ ...p, num: null, sub: null }))).map((s: Pick<StatCard, "label" | "tone" | "icon"> & { num: number | null; sub: string | null }) => (
          <Stat
            key={s.label}
            value={s.num ?? (statsQ.loading ? <Spinner size="sm" /> : "—")}
            label={s.label}
            sub={s.sub ?? (statsQ.loading ? "loading" : "API offline")}
            tone={s.tone as StatTone}
            swatch={<Hatch color={STAT_COLORS[s.tone]} />}
            ring={<IconRing tone={s.tone as IconRingTone}>{STAT_ICONS[s.icon]}</IconRing>}
            onClick={() => navigate(STAT_LINKS[s.icon])}
          />
        ))}
      </div>

      {/* rows 2–4: one shared two-track grid so every card edge lines up */}
      <div className="ov-grid">
        {/* This week's digest */}
        <DigestCard />

        {/* Recent docs — right track */}
        <div className="card lineage" style={{ display: "flex", flexDirection: "column" }}>
          <div className="card__head">
            <h3 className="card__title">Recent docs</h3>
            <span className="card__spacer" />
            <Link to="/knowledge" className="btn"><Ic.Expand size={14} /> Browse</Link>
          </div>
          <div className="ovdocs" style={{ flex: 1, minHeight: 0 }}>
            {recentQ.loading && <Loading minHeight={60} />}
            {recentQ.error && !recentQ.data && <EmptyState>API offline — recent docs unavailable.</EmptyState>}
            {recentDocs.map((d) => (
              <Link key={d.id} to={`/knowledge/doc?id=${d.id}`} className="ovdocs__row">
                <SourceIcon source={d.source as SourceKey} size={17} />
                <span className="ovdocs__title">{d.title}</span>
                <span className="ovdocs__date">{fmtDate(d.date)}</span>
              </Link>
            ))}
          </div>
        </div>

        {/* Today's review — left track */}
        <div className="card review">
          <div className="card__head">
            <IconRing><Ic.Clipboard size={16} /></IconRing>
            <h3 className="card__title">Today’s review</h3>
          </div>
          {tasks === null ? (
            tasksError
              ? <EmptyState>API offline — tasks unavailable.</EmptyState>
              : <Loading minHeight={120} />
          ) : (
          <div className="review__list">
            {visibleTasks.map((t) => (
              <div className="review__item" key={t.id ?? t.text}>
                <button
                  className={`review__check${t.done ? " on" : ""}`}
                  onClick={() => toggle(t)}
                  aria-label={t.done ? "Mark undone" : "Mark done"}
                >
                  <Ic.Check size={11} strokeWidth={2.4} />
                </button>
                <span className={`review__text${t.done ? " done" : ""}`}>{t.text}</span>
                <Avatar name={t.who} initials={t.who} tint={t.tint as 1 | 2 | 3 | 4} size="sm" />
                <Pill kind={t.pill} text={t.pillText} />
              </div>
            ))}
          </div>
          )}
          <div className="review__foot">
            {anyDone ? (
              <button className="review__clear" onClick={clearDone}>
                <Ic.Trash size={13} /> Clear done
              </button>
            ) : (
              <span className="card__hint">{(tasks ?? []).filter((t) => !t.done).length} open</span>
            )}
            <Link to="/tasks" className="review__more">
              View all tasks <span className="countbubble">{(tasks ?? []).length}</span> <Ic.ChevR size={15} />
            </Link>
          </div>
        </div>

        {/* Source pulse — right track */}
        <div className="card pulse">
          <div className="card__head">
            <IconRing><Ic.Sparkle size={16} /></IconRing>
            <h3 className="card__title">Source pulse</h3>
            <span className="card__spacer" />
            <span className="card__hint">Last 7 days</span>
          </div>
          {pulseQ.loading && <Loading minHeight={120} />}
          {pulseQ.error && !pulseQ.data && <EmptyState>API offline — source pulse unavailable.</EmptyState>}
          {pulseQ.data && (
          <div className="pulse__grid">
            {pulseQ.data.map((s) => (
              <div className="pulse__tile" key={s.key}>
                <SourceIcon source={s.key} size={26} />
                <span className="pulse__name">{s.name}</span>
                <span className="pulse__stat">{s.stat}<br />{s.unit}</span>
                <Bars values={s.bars} width={56} height={22} color={s.status === "moderate" ? "var(--gold)" : "var(--green)"} />
                <span className={`pulse__status pulse__status--${s.status}`}>
                  <span className="dot" />
                  {s.status === "moderate" ? "Moderate" : "Active"}
                </span>
              </div>
            ))}
          </div>
          )}
          <Link to="/settings/sources" className="pulse__foot">
            View all sources <Ic.ChevR size={14} />
          </Link>
        </div>

        {/* Live activity — left track */}
        <div className="card live">
          <div className="card__head">
            <span className="live__dot" aria-hidden="true" />
            <h3 className="card__title">Live</h3>
            <span className="card__spacer" />
            <span className="card__hint">Runs &amp; edits · refreshes every 10s</span>
          </div>
          <div className="live__list">
            {feedQ.loading && <Loading minHeight={60} />}
            {feedQ.error && !feedQ.data && <EmptyState>API offline — activity unavailable.</EmptyState>}
            {feedRows.map((e) => (
              <div className="live__row" key={e.id}>
                <span className="live__icon">{FEED_ICONS[e.kind] ?? <Ic.Pencil size={14} />}</span>
                <span className="live__text">
                  {e.actor} {e.text} <em className="live__target">{e.target}</em>
                </span>
                <span className="live__at">{fmtDateTime(e.at)}</span>
              </div>
            ))}
            {feedQ.data && feedRows.length === 0 && <div className="live__row"><span className="live__text">Quiet for now — nothing running.</span></div>}
          </div>
        </div>

        {/* Workflow — left track (the digest shifted the right track down one row) */}
        <div className="card wf">
          <div className="card__head">
            <IconRing><Ic.Flow size={16} /></IconRing>
            <h3 className="card__title">Workflow</h3>
            <span className="card__spacer" />
            <Link to="/flows" className="btn"><Ic.Gear size={14} /> Configure</Link>
          </div>
          {wfQ.loading && <Loading minHeight={120} />}
          {wfQ.error && !wfQ.data && <EmptyState>API offline — flows unavailable.</EmptyState>}
          {wfQ.data && !stripWf && <EmptyState>No flows yet — create one in Flows.</EmptyState>}
          {stripWf && (
            <>
              <div className="wf__steps">
                {(stripWf.nodes ?? []).slice(0, 4).map((s, i, arr) => {
                  const StepIcon = KIND_ICON[s.kind as StepKind] ?? Ic.Flow;
                  const sec = SECTION_OF[s.kind as StepKind];
                  const tone = sec === "when" ? "green" : sec === "do" ? "blue" : sec === "check" ? "check" : "red";
                  return (
                    <Fragment key={`${s.kind}-${i}`}>
                      <div className="wf__step">
                        <span className={`wf__bubble wf__bubble--${tone}`}>
                          <StepIcon size={20} />
                        </span>
                        <span className="wf__label">{s.label}</span>
                      </div>
                      {i < arr.length - 1 && <span className="wf__dots" style={{ minWidth: 14 }} />}
                    </Fragment>
                  );
                })}
              </div>
              <div className="wf__meta">
                <div className="wf__meta-row">
                  <span style={{ fontWeight: 600 }}>{stripWf.name}</span>
                  <span className="wf__running">
                    <span className="dot" /> {stripWf.status === "active" ? "Active" : "Paused"}
                  </span>
                </div>
                <div className="wf__meta-row">
                  <span className="sub">
                    {stripRun
                      ? `Last run: ${fmtStarted(stripRun.started)}`
                      : wfRunsQ.loading ? "Loading runs…" : "Never run"}
                  </span>
                  {stripRun && (
                    <span className="who">{normStatus(stripRun.status) === "passed" ? "Passed" : stripRun.status}</span>
                  )}
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </>
  );
}
