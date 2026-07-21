// Node detail drawer — the product-standard fl-drawer pattern (.lg-drawer),
// kept bespoke rather than the ui Drawer so the canvas stays interactive with
// no focus trap and Esc keeps its page-level priority ordering.

import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import * as Ic from "../../components/icons";
import { Pill, SourceIcon } from "../../components/shared";
import { Button, Chip, EmptyState, Spinner, StatusChip, Tabs, fmtDate } from "../../components/ui";
import { SourceKey } from "../../data/sources";
import { gql, gqlResult, useQuery } from "../../lib/api";
import { nodeIcon, PinGlyph } from "./glyphs";
import {
  downloadText, LayoutMode, LEdge, LNode, REL, REL_IN, REL_OUT, RelKey, staleColor,
  type DocHistoryRow,
} from "./model";

type DocDetail = {
  id: number; source: string; title: string; snippet: string; kind: string;
  author: string; date: string; tags: string[]; watched: boolean;
};
type LiveState =
  | { status: "loading" }
  | { status: "offline" }
  | { status: "unresolved" }
  | { status: "ready"; doc: DocDetail };
type Fact = { id: number; claim: string; source: string; owner: string; status: string; verified: string };

type PanelTab = "Overview" | "Connections" | "History" | "Impact";
const PANEL_TABS: PanelTab[] = ["Overview", "Connections", "History", "Impact"];

export type TraceRows = { dir: "down" | "up"; groups: Map<RelKey, { id: string; edge: LEdge }[]>; changedSince: Set<string> } | null;

export function NodeDrawer({ node, nodesById, edges, pinned, layout, hiddenNeighbors, groupChip, traceRows, onSelect, onFocus, onTrace, onExpand, onPin, onUnpin, onClose }: {
  node: LNode;
  nodesById: Record<string, LNode>;
  edges: LEdge[];
  pinned: boolean;
  layout: LayoutMode;
  hiddenNeighbors: number;
  groupChip: { label: string; onCollapse: () => void } | null; // member of an expanded roll-up
  traceRows: TraceRows;
  onSelect: (id: string) => void;
  onFocus: (id: string) => void;
  onTrace: (dir: "down" | "up") => void;
  onExpand: () => void;
  onPin: () => void;
  onUnpin: () => void;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<PanelTab>("Overview");
  const [live, setLive] = useState<LiveState>({ status: "loading" });
  // facts rarely change; useQuery's shared cache makes this one fetch per app
  // load across drawer opens — and it's dropped on sign-out with everything else
  const factsQ = useQuery<Fact[]>(`{ facts { id claim source owner status verified } }`, { map: (d) => d.facts ?? [] });
  const facts = factsQ.data;
  const [watchOverride, setWatchOverride] = useState<boolean | null>(null);
  const [watchBusy, setWatchBusy] = useState(false);
  const [taskState, setTaskState] = useState<"idle" | "creating" | "done" | "error">("idle");
  // undefined = not fetched yet; null = fetch failed / offline
  const [history, setHistory] = useState<DocHistoryRow[] | null | undefined>(undefined);
  const [copied, setCopied] = useState(false);

  // the graph node carries the real document id — fetch it directly.
  // The drawer stays mounted while the target node changes (no re-slide
  // animation), so per-node state is reset here rather than via a key remount.
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    let alive = true;
    setWatchOverride(null);
    setWatchBusy(false);
    setTaskState("idle");
    setTab("Overview");
    setHistory(undefined);
    setCopied(false);
    if (node.docId == null) { setLive({ status: "unresolved" }); return; }
    setLive({ status: "loading" });
    (async () => {
      const r = await gqlResult<{ document: DocDetail | null }>(
        `query($id: Int!) { document(id: $id) { id source title snippet kind author date tags watched } }`,
        { id: node.docId },
      );
      if (!alive) return;
      if (!r.ok) setLive({ status: "offline" });
      else if (r.data.document) setLive({ status: "ready", doc: r.data.document });
      else setLive({ status: "unresolved" });
    })();
    return () => { alive = false; };
  }, [node.id, node.docId]);
  /* eslint-enable react-hooks/set-state-in-effect */

  // docHistory fetched lazily when the History tab opens
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (tab !== "History" || history !== undefined) return;
    if (node.docId == null) { setHistory(null); return; }
    let alive = true;
    gql(`query($id: Int!) { docHistory(documentId: $id) { at actor verb detail } }`, { id: node.docId })
      .then((d: any) => { if (alive) setHistory(d?.docHistory ?? null); });
    return () => { alive = false; };
  }, [tab, history, node.docId]);

  // an active trace opens straight onto its closure list
  useEffect(() => {
    if (traceRows) setTab("Impact");
  }, [traceRows?.dir]); // eslint-disable-line react-hooks/exhaustive-deps
  /* eslint-enable react-hooks/set-state-in-effect */

  const conns = useMemo(
    () => edges.filter((e) => (e.from === node.id || e.to === node.id) && nodesById[e.from] && nodesById[e.to]),
    [edges, node.id, nodesById],
  );

  // connections grouped by edge type + direction, with counts
  const connGroups = useMemo(() => {
    const m = new Map<string, { label: string; rel: RelKey; rows: { edge: LEdge; otherId: string; out: boolean }[] }>();
    for (const e of conns) {
      const out = e.from === node.id;
      const label = out ? REL_OUT[e.rel] : REL_IN[e.rel];
      const key = `${e.rel}:${out}`;
      if (!m.has(key)) m.set(key, { label, rel: e.rel, rows: [] });
      m.get(key)!.rows.push({ edge: e, otherId: out ? e.to : e.from, out });
    }
    return [...m.values()];
  }, [conns, node.id]);

  // extracted `references` edges, surfaced on pr/issue/commit overviews
  // (LINEAGE-ROLLUP-CONTRACT: "Closes #12", merge-commit "(#N)" links)
  const referenceRows = useMemo(() => {
    if (node.docKind !== "pr" && node.docKind !== "issue" && node.docKind !== "commit") return null;
    return conns
      .filter((e) => e.rel === "references")
      .map((e) => ({ edge: e, otherId: e.from === node.id ? e.to : e.from, out: e.from === node.id }));
  }, [conns, node.id, node.docKind]);

  // upstream/downstream closures over directed edges (client-side BFS)
  const impact = useMemo(() => {
    const walk = (dir: "down" | "up") => {
      const seen = new Set([node.id]);
      const rows: { id: string; rel: RelKey; date?: string }[] = [];
      const q = [node.id];
      while (q.length) {
        const cur = q.shift()!;
        for (const e of edges) {
          const nxt = dir === "down" ? (e.from === cur ? e.to : null) : (e.to === cur ? e.from : null);
          if (nxt && !seen.has(nxt) && nodesById[nxt]) {
            seen.add(nxt);
            rows.push({ id: nxt, rel: e.rel, date: e.date });
            q.push(nxt);
          }
        }
      }
      return rows;
    };
    return { down: walk("down"), up: walk("up") };
  }, [edges, node.id, nodesById]);

  const exportCsv = () => {
    const esc = (s: unknown) => `"${String(s ?? "").replace(/"/g, '""')}"`;
    const lines = ["title,source,direction,edgeKind,date"];
    for (const [dir, rows] of [["downstream", impact.down], ["upstream", impact.up]] as const) {
      for (const r of rows) {
        const n2 = nodesById[r.id];
        if (n2) lines.push([esc(n2.title), esc(n2.source), dir, r.rel, r.date ?? ""].join(","));
      }
    }
    downloadText(`impact-${node.id}.csv`, lines.join("\n"), "text/csv");
  };

  // best-effort: verified facts whose claim/source shares a keyword with the title
  const relatedFacts = useMemo(() => {
    if (!facts) return [];
    const kws = node.title.toLowerCase().split(/[^a-z0-9]+/).filter((w) => w.length >= 3);
    return facts
      .filter((f) => f.status === "Verified" && kws.some((k) => f.claim.toLowerCase().includes(k) || f.source.toLowerCase().includes(k)))
      .slice(0, 3);
  }, [facts, node.title]);

  const watched = watchOverride ?? (live.status === "ready" ? live.doc.watched : false);

  const toggleWatch = async () => {
    if (live.status !== "ready" || watchBusy) return;
    setWatchBusy(true);
    const d: any = await gql(`mutation($id: Int!) { toggleWatch(documentId: $id) }`, { id: live.doc.id });
    setWatchBusy(false);
    if (d && typeof d.toggleWatch === "boolean") setWatchOverride(d.toggleWatch);
    else if (d) setWatchOverride(!watched);
  };

  const createReview = async () => {
    if (taskState === "creating" || taskState === "done") return;
    setTaskState("creating");
    const d = await gql(
      `mutation($title: String!, $kind: String!, $kindLabel: String!) { createTask(title: $title, kind: $kind, kindLabel: $kindLabel) }`,
      { title: `Review: ${node.title}`, kind: "factcheck", kindLabel: "Review" },
    );
    setTaskState(d ? "done" : "error");
  };

  const copyLink = () => {
    navigator.clipboard?.writeText(window.location.href)
      .then(() => { setCopied(true); setTimeout(() => setCopied(false), 1600); })
      .catch(() => {});
  };

  const docHref = live.status === "ready"
    ? `/knowledge/doc?id=${live.doc.id}`
    : node.docId != null ? `/knowledge/doc?id=${node.docId}` : "/knowledge";
  const metaPills = node.meta.split("·").map((p) => p.trim()).filter(Boolean);

  return (
    <aside className="lg-drawer" role="dialog" aria-label={`Details for ${node.title}`}>
      <div className="lg-drawer__head">
        <span className="lg-drawer__icon">{nodeIcon(node.icon, node.source)}</span>
        <div className="lg-grow">
          <b className="lg-drawer__title">{node.title}</b>
          <div className="lg-drawer__pills">
            {metaPills.map((p) => <Chip key={p}>{p}</Chip>)}
            {node.warn
              ? <StatusChip status="stale" label="Needs attention" />
              : <Chip tone="green" dot>Healthy</Chip>}
            {pinned && <Chip icon={<PinGlyph size={9} />} title="Position pinned by hand">Pinned</Chip>}
            {groupChip && (
              <Chip onClick={groupChip.onCollapse} title="Collapse this group back to one node">
                ⊖ {groupChip.label}
              </Chip>
            )}
          </div>
        </div>
        <button className="kebab lg-drawer__close" onClick={onClose} aria-label="Close details drawer">✕</button>
      </div>

      {/* tab strip */}
      <div className="lg-drawer__tabs">
        <Tabs
          variant="underline"
          ariaLabel="Node detail sections"
          value={tab}
          options={PANEL_TABS.map((t) => ({ id: t, label: t }))}
          onChange={setTab}
        />
      </div>

      <div className="lg-drawer__body">
        {tab === "Overview" && (
          <>
            {live.status === "loading" && <div className="lg-hint"><Spinner size="sm" /> Fetching live details…</div>}
            {live.status === "offline" && <div className="lg-hint">Mari API is offline — showing map data only.</div>}
            {live.status === "unresolved" && <div className="lg-hint">No indexed document for this node yet.</div>}
            {live.status === "ready" && (
              <div>
                <span className="lg-label">Document</span>
                <div className="lg-kv"><span>Owner</span><b>{live.doc.author}</b></div>
                <div className="lg-kv"><span>Updated</span><b>{live.doc.date}</b></div>
                <div className="lg-kv">
                  <span>Source</span>
                  <b className="row" style={{ gap: 5 }}>
                    <SourceIcon source={live.doc.source as SourceKey} size={13} /> {live.doc.source}
                  </b>
                </div>
                {node.staleDays != null && (
                  <div className="lg-kv"><span>Staleness</span><b style={{ color: staleColor(node.staleDays) }}>{node.staleDays} days</b></div>
                )}
                {live.doc.tags.length > 0 && (
                  <div className="lg-drawer__pills">
                    {live.doc.tags.map((t) => <Pill key={t} kind={t} />)}
                  </div>
                )}
                {live.doc.snippet && <div className="lg-summary">{live.doc.snippet}</div>}
              </div>
            )}
            {live.status !== "ready" && (
              <div>
                <span className="lg-label">From the graph</span>
                {node.owner && <div className="lg-kv"><span>Owner</span><b>{node.owner}</b></div>}
                {node.date && <div className="lg-kv"><span>Updated</span><b>{fmtDate(node.date)}</b></div>}
                {node.staleDays != null && (
                  <div className="lg-kv"><span>Staleness</span><b style={{ color: staleColor(node.staleDays) }}>{node.staleDays} days</b></div>
                )}
              </div>
            )}
            {referenceRows && (
              <div className="lg-group">
                <span className="lg-label" style={{ color: REL.references.color }}>
                  References ({referenceRows.length})
                </span>
                {referenceRows.length === 0 && <div className="lg-hint">No extracted references yet.</div>}
                {referenceRows.map(({ edge, otherId, out }, i) => (
                  <button key={i} className="lg-conn" onClick={() => onSelect(otherId)} title={`Show ${nodesById[otherId]?.title} in this drawer`}>
                    <svg width="18" height="6" className="lg-conn__line" aria-hidden="true">
                      <line x1="0" y1="3" x2="18" y2="3" stroke={REL.references.color} strokeWidth="2" strokeDasharray={edge.llm || edge.dashed ? "4 4" : undefined} />
                    </svg>
                    <span className="lg-conn__main">
                      <b>{out ? "→ " : "← "}{nodesById[otherId]?.title ?? otherId}</b>
                    </span>
                    <span
                      role="button"
                      tabIndex={0}
                      className="lg-focusbtn"
                      title="Set graph focus here"
                      onClick={(ev) => { ev.stopPropagation(); onFocus(otherId); }}
                      onKeyDown={(ev) => { if (ev.key === "Enter") { ev.stopPropagation(); onFocus(otherId); } }}
                    >
                      ⌖ focus
                    </span>
                  </button>
                ))}
              </div>
            )}
            {relatedFacts.length > 0 && (
              <div>
                <span className="lg-label">Verified facts</span>
                {relatedFacts.map((f) => (
                  <div key={f.id} className="lg-fact">
                    <b>{f.claim}</b>
                    {f.source} · {f.owner} · verified {f.verified}
                  </div>
                ))}
              </div>
            )}
          </>
        )}

        {tab === "Connections" && (
          <div>
            {connGroups.length === 0 && <EmptyState>No links recorded for this node.</EmptyState>}
            {connGroups.map((g) => (
              <div key={`${g.rel}-${g.label}`} className="lg-group">
                <span className="lg-label" style={{ color: REL[g.rel].color }}>
                  {g.label} ({g.rows.length})
                </span>
                {g.rows.map(({ edge, otherId, out }, i) => {
                  const detail = edge.meta?.note ?? edge.meta?.evidence;
                  return (
                    <button key={i} className="lg-conn" onClick={() => onSelect(otherId)} title={`Show ${nodesById[otherId]?.title} in this drawer`}>
                      <svg width="18" height="6" className="lg-conn__line" aria-hidden="true">
                        <line x1="0" y1="3" x2="18" y2="3" stroke={REL[edge.rel].color} strokeWidth="2" strokeDasharray={edge.llm || edge.dashed ? "4 4" : REL[edge.rel].dash} />
                      </svg>
                      <span className="lg-conn__main">
                        <b>{out ? "→ " : "← "}{nodesById[otherId]?.title ?? otherId}</b>
                        {detail && <span className="lg-conn__sub">{detail}</span>}
                      </span>
                      <span
                        role="button"
                        tabIndex={0}
                        className="lg-focusbtn"
                        title="Set graph focus here"
                        onClick={(ev) => { ev.stopPropagation(); onFocus(otherId); }}
                        onKeyDown={(ev) => { if (ev.key === "Enter") { ev.stopPropagation(); onFocus(otherId); } }}
                      >
                        ⌖ focus
                      </span>
                    </button>
                  );
                })}
              </div>
            ))}
          </div>
        )}

        {tab === "History" && (
          <div>
            <span className="lg-label">Document history</span>
            {history === undefined && <div className="lg-hint"><Spinner size="sm" /> Loading history…</div>}
            {history === null && <div className="lg-hint">No history available (API offline or not indexed).</div>}
            {history && history.length === 0 && <EmptyState>No recorded events for this document.</EmptyState>}
            {history && history.map((h, i) => (
              <div key={i} className="lg-hist">
                <span className="lg-hist__dot" />
                <span className="lg-hist__main">
                  <b>{h.actor}</b> {h.verb}
                  {h.detail && <span className="lg-hist__sub">{h.detail}</span>}
                  <span className="lg-hist__at">{h.at}</span>
                </span>
              </div>
            ))}
          </div>
        )}

        {tab === "Impact" && (
          <div className="lg-stack">
            <div>
              <span className="lg-label">Closure</span>
              <div className="lg-kv"><span>Downstream (depends on this)</span><b>{impact.down.length}</b></div>
              <div className="lg-kv"><span>Upstream (this rests on)</span><b>{impact.up.length}</b></div>
            </div>
            <div className="lg-actions">
              <Button compact onClick={() => onTrace("down")} disabled={impact.down.length === 0}>
                <Ic.ArrowR size={13} /> Trace impact
              </Button>
              <Button compact onClick={() => onTrace("up")} disabled={impact.up.length === 0}>
                <Ic.LineageIcon size={13} /> Trace provenance
              </Button>
              <Button compact onClick={exportCsv} disabled={impact.down.length + impact.up.length === 0}>
                <Ic.Send size={13} /> Export CSV
              </Button>
            </div>
            {traceRows ? (
              <div>
                <span className="lg-label">{traceRows.dir === "down" ? "Impact closure" : "Provenance closure"}</span>
                {traceRows.groups.size === 0 && (
                  <div className="lg-hint">Nothing {traceRows.dir === "down" ? "downstream" : "upstream"} of this document.</div>
                )}
                {[...traceRows.groups.entries()].map(([rel, rows]) => (
                  <div key={rel} className="lg-group lg-group--tight">
                    <span className="lg-label lg-label--tight" style={{ color: REL[rel].color }}>
                      {REL[rel].label} ({rows.length})
                    </span>
                    {rows.map((r) => (
                      <button key={r.id} className="lg-conn" onClick={() => onSelect(r.id)} title={`Show details for ${nodesById[r.id]?.title}`}>
                        <span className="lg-conn__main lg-ellip">
                          <b>{nodesById[r.id]?.title ?? r.id}</b>
                        </span>
                        {traceRows.changedSince.has(r.id) && <span className="lchanged">changed since</span>}
                        {r.edge.date && <span className="card__hint lg-nowrap">{fmtDate(r.edge.date)}</span>}
                      </button>
                    ))}
                  </div>
                ))}
              </div>
            ) : (
              <div className="lg-hint">Tracing highlights the closure on the canvas and lists it here.</div>
            )}
          </div>
        )}
      </div>

      <div className="lg-drawer__foot">
        <div className="lg-actions">
          <Button compact onClick={() => onFocus(node.id)} title="Make this the focal node">
            ⌖ Set focal
          </Button>
          {hiddenNeighbors > 0 && (
            <Button compact onClick={onExpand} title={`Show ${hiddenNeighbors} hidden neighbor${hiddenNeighbors === 1 ? "" : "s"}`}>
              <Ic.Expand size={12} /> Expand neighbors (+{hiddenNeighbors})
            </Button>
          )}
          {layout === "timeline" && (
            <Button compact onClick={pinned ? onUnpin : onPin} disabled={node.docId == null} title={pinned ? "Release the pinned position (back to auto-layout)" : "Pin the current position"}>
              <PinGlyph size={11} /> {pinned ? "Unpin" : "Pin"}
            </Button>
          )}
          <Button compact onClick={copyLink} title="Copy a deep link to this view">
            <Ic.External size={12} /> {copied ? "Copied" : "Copy link"}
          </Button>
        </div>
        {live.status === "ready" && (
          <div className="lg-actions">
            <Button compact className={watched ? "lg-on" : undefined} onClick={toggleWatch} disabled={watchBusy}>
              <Ic.Eye size={13} /> {watched ? "Watching" : "Watch"}
            </Button>
            <Button compact onClick={createReview} disabled={taskState === "creating" || taskState === "done"}>
              {taskState === "done"
                ? <><Ic.Check size={13} strokeWidth={2.2} /> Task created</>
                : <><Ic.Clipboard size={13} /> {taskState === "creating" ? "Creating…" : "Create review task"}</>}
            </Button>
          </div>
        )}
        {taskState === "error" && <span className="lg-hint">Couldn’t reach Mari — task not created.</span>}
        <Link className="linklike lg-openlink" to={docHref}>
          Open document <Ic.External size={11} />
        </Link>
      </div>
    </aside>
  );
}
