// Product lineage — the document-graph entrypoint, rendered with Cytoscape.js:
// search-first toolbar, dagre/timeline layouts, focal model with counted
// expansion, event-anchored time travel, lenses, trace modes, ecosystem strip,
// saved views, and deep links (LINEAGE-DESIGN §4). Nodes and edges come from
// the API (lineage / lineageEdges). The graph logic here is carefully tuned —
// treat as fragile.

import cytoscape, { Core } from "cytoscape";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import * as Ic from "../../components/icons";
import { Button, Chip, EmptyState, Spinner } from "../../components/ui";
import { gql, useQuery } from "../../lib/api";
import "../lineage.css";
import { AssertDrawer } from "./AssertDrawer";
import { CY_STYLE } from "./cy";
import { EdgeDrawer } from "./EdgeDrawer";
import { GroupDrawer } from "./GroupDrawer";
import {
  AGG_EDGE_PREFIX, clamp, edgeKey, GraphStats, GraphView,
  GROUP_PAGE_SIZE, groupKindWord, groupParts, isoToDate, KIND_TO_REL,
  LayoutMode, LEdge, Lens, LENSES, LINEAGE_QUERY,
  LNode, MACRO_PREFIX, ownerColor, readUrl, REL, RelKey, Sel, SEVERITY_TASK,
  SOURCE_ACCENT, SOURCE_GLYPH, SOURCE_LABELS, SOURCE_ORDER, srcKey,
  staleColor, STATS_QUERY, STUB_PREFIX, TL_H, TL_W, ViewState, downloadText,
  type DocKind, type ImpactResult,
} from "./model";
import { NodeDrawer } from "./NodeDrawer";
import { Scrubber } from "./Scrubber";
import { Toolbar } from "./Toolbar";

export default function LineagePage() {
  const navigate = useNavigate();
  const [urlInit] = useState(readUrl);

  // ————— shareable view state (URL + saved views) —————
  const [focus, setFocusRaw] = useState<string | null>(urlInit.focus);
  const [lens, setLens] = useState<Lens>(urlInit.lens);
  const [asof, setAsof] = useState<string | null>(urlInit.asof); // ISO or null = all time
  const [scope, setScope] = useState<"focus" | "all">(urlInit.scope);
  const [chip, setChip] = useState<string | null>(urlInit.chip);
  const [relsOn, setRelsOn] = useState<Record<RelKey, boolean>>(
    Object.fromEntries((Object.keys(REL) as RelKey[]).map((k) => [k, urlInit.rels.includes(k)])) as Record<RelKey, boolean>,
  );
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  // roll-up: expanded macro groups (?open=) + how many member pages each shows
  const [openGroups, setOpenGroups] = useState<Set<string>>(new Set(urlInit.open));
  const [groupPages, setGroupPages] = useState<Record<string, number>>({});

  // ————— local UI state —————
  const [layout, setLayout] = useState<LayoutMode>("flow");
  // sources default to on; toggling records false explicitly
  const [sourcesOn, setSourcesOn] = useState<Record<string, boolean>>({});
  const srcOn = (s: string) => sourcesOn[s] !== false;
  const [statusFilter, setStatusFilter] = useState<"All" | "Warnings">("All");
  const [pathMode, setPathMode] = useState(false);
  const [pathPicks, setPathPicks] = useState<string[]>([]);
  const [deriving, setDeriving] = useState(false);
  const [sel, setSel] = useState<Sel | null>(null);
  const [trace, setTrace] = useState<{ nodeId: string; dir: "down" | "up" } | null>(null);
  const [assertOpen, setAssertOpen] = useState(false);
  const [q, setQ] = useState("");
  const [pinnedLocal, setPinnedLocal] = useState<Record<string, boolean>>({});
  const [zoomPct, setZoomPct] = useState(100);
  const [tip, setTip] = useState<{ id: string; x: number; y: number; w: number } | null>(null);

  // impact / assertion drawer state (kept here so results survive reopen)
  const [claim, setClaim] = useState("Free tier ends September 1");
  const [analyzing, setAnalyzing] = useState(false);
  const [result, setResult] = useState<ImpactResult | null>(null);
  const [analyzedAt, setAnalyzedAt] = useState<string | null>(null);
  const [taskState, setTaskState] = useState<"idle" | "creating" | "done" | "error">("idle");
  const [severityFilter, setSeverityFilter] = useState<string | null>(null);

  const panelNode = sel?.kind === "node" ? sel.id : null;

  const focusOn = (id: string | null) => {
    setFocusRaw(id);
    setExpanded(new Set());
    if (id == null) setTrace(null);
  };
  // clicking a node opens the drawer in place — never navigates; grouped
  // members get their roll-up expanded first so the node is on the canvas
  const openNode = (id: string) => { revealNode(id); setSel({ kind: "node", id }); setAssertOpen(false); };
  const pickNode = (id: string) => {
    setPathPicks((p) => {
      if (p.includes(id)) return p.filter((x) => x !== id); // re-click deselects
      if (p.length >= 2) return [id];
      return [...p, id];
    });
  };
  const activateNode = (id: string) => {
    if (pathMode) pickNode(id);
    else openNode(id);
  };

  // ————— data —————
  const graphQ = useQuery<{ nodes: LNode[]; edges: LEdge[] }>(LINEAGE_QUERY, {
    map: (d) => ({
      nodes: (d.lineage ?? []).map((n: any): LNode => ({
        id: String(n.id),
        docId: n.docId ?? undefined,
        source: n.source ?? "docs",
        // classification per LINEAGE-ROLLUP-CONTRACT §Server half
        docKind: (n.docKind as DocKind) ?? "page",
        group: (n.group as string) ?? "",
        title: n.title ?? "",
        meta: n.meta ?? "",
        icon: n.icon ?? "doc",
        x: n.x ?? 0.5,
        y: n.y ?? 0.5,
        date: n.date ?? undefined,
        createdDate: n.createdDate ?? n.date ?? undefined,
        pinned: !!n.pinned,
        warn: !!n.warn,
        owner: n.owner ?? "",
        tags: n.tags ?? [],
        staleDays: n.staleDays ?? undefined,
        orphan: n.orphan ?? undefined,
        inbound: n.inbound ?? undefined,
        outbound: n.outbound ?? undefined,
      })),
      edges: (() => {
        // several api kinds map onto one rel (links_to/similar/references →
        // references): dedupe on (from, to, rel) so cy element ids stay unique
        const seen = new Set<string>();
        return (d.lineageEdges ?? [])
          .map((e: any): LEdge => ({
            from: String(e.fromId),
            to: String(e.toId),
            rel: KIND_TO_REL[e.kind] ?? "references",
            date: e.date ?? undefined,
            llm: e.meta?.derived === "llm",
            // similar edges are embedding-derived, not authored — render dashed
            dashed: e.kind === "reference" || e.kind === "similar" || e.meta?.derived === "llm",
            meta: e.meta ?? {},
          }))
          .filter((e: LEdge) => {
            const k = edgeKey(e);
            if (seen.has(k)) return false;
            seen.add(k);
            return true;
          });
      })(),
    }),
  });

  const EMPTY_GRAPH = useMemo(() => ({ nodes: [] as LNode[], edges: [] as LEdge[] }), []);
  const { nodes, edges } = graphQ.data ?? EMPTY_GRAPH;

  // ecosystem stats + saved views (fetched separately so a missing resolver
  // never takes the graph down with it; null → the strip simply hides)
  const statsQ = useQuery<GraphStats | null>(STATS_QUERY, { map: (d) => d.graphStats ?? null });
  const stats = statsQ.data ?? null;
  const viewsQ = useQuery<GraphView[]>(`{ graphViews { id name state } }`, { map: (d) => d.graphViews ?? [] });
  const views = viewsQ.data ?? [];

  const refetchGraph = () => {
    graphQ.refetch();
    statsQ.refetch();
  };

  const nodeById = useMemo(() => Object.fromEntries(nodes.map((n) => [n.id, n])), [nodes]);
  const edgeByKey = useMemo(() => Object.fromEntries(edges.map((e) => [edgeKey(e), e])), [edges]);

  // undirected adjacency (focal closures, +N counts, orphan fallback)
  const adj = useMemo(() => {
    const m = new Map<string, Set<string>>();
    for (const e of edges) {
      if (!m.has(e.from)) m.set(e.from, new Set());
      if (!m.has(e.to)) m.set(e.to, new Set());
      m.get(e.from)!.add(e.to);
      m.get(e.to)!.add(e.from);
    }
    return m;
  }, [edges]);

  const inboundOf = useMemo(() => {
    const m = new Map<string, number>();
    for (const e of edges) m.set(e.to, (m.get(e.to) ?? 0) + 1);
    return (n: LNode) => n.inbound ?? m.get(n.id) ?? 0;
  }, [edges]);
  const isOrphan = (n: LNode) => n.orphan ?? !(adj.get(n.id)?.size);

  // health lens helper sets
  const contraInbound = useMemo(() => {
    const s = new Set<string>();
    for (const e of edges) if (e.rel === "contradicts") s.add(e.to);
    return s;
  }, [edges]);
  const contraAny = useMemo(() => {
    const s = new Set<string>();
    for (const e of edges) if (e.rel === "contradicts") { s.add(e.from); s.add(e.to); }
    return s;
  }, [edges]);
  const lagIds = useMemo(() => {
    // translation lag: node is source of a translates edge whose target is older
    const s = new Set<string>();
    for (const e of edges) {
      if (e.rel !== "translates") continue;
      const a = nodeById[e.from], b = nodeById[e.to];
      if (a?.date && b?.date && b.date < a.date) s.add(e.from);
    }
    return s;
  }, [edges, nodeById]);
  const untranslatedIds = useMemo(() => {
    const translated = new Set(edges.filter((e) => e.rel === "translates").map((e) => e.from));
    const s = new Set<string>();
    for (const n of nodes) if ((n.tags ?? []).includes("customer-facing") && !translated.has(n.id)) s.add(n.id);
    return s;
  }, [nodes, edges]);

  // ————— source groups (filter menu) —————
  const sourceGroups = useMemo(() => {
    const groups = new Map<string, number>();
    for (const n of nodes) {
      const k = srcKey(n.source);
      groups.set(k, (groups.get(k) ?? 0) + 1);
    }
    return SOURCE_ORDER.filter((s) => groups.has(s)).map((s) => ({
      source: s,
      label: SOURCE_LABELS[s],
      count: groups.get(s)!,
    }));
  }, [nodes]);

  // ————— event-anchored time —————
  const activity = useMemo(() => {
    if (stats?.activity?.length) return stats.activity;
    // API-down: synthesize density from the dates we do have
    const m = new Map<string, number>();
    const add = (d?: string) => { if (d) m.set(d, (m.get(d) ?? 0) + 1); };
    for (const n of nodes) { add(n.date); add(n.createdDate); }
    for (const e of edges) add(e.date);
    return [...m.entries()].map(([date, count]) => ({ date, count })).sort((a, b) => a.date.localeCompare(b.date));
  }, [stats, nodes, edges]);

  const eventDates = useMemo(() => {
    const s = new Set<string>();
    for (const n of nodes) { if (n.date) s.add(n.date); if (n.createdDate) s.add(n.createdDate); }
    for (const e of edges) if (e.date) s.add(e.date);
    for (const a of activity) if (a.date) s.add(a.date);
    return [...s].sort();
  }, [nodes, edges, activity]);

  // snap the as-of date to the nearest event date at or before it
  const effAsof = useMemo(() => {
    if (asof == null || eventDates.length === 0) return null;
    let snapped: string | null = null;
    for (const d of eventDates) { if (d <= asof) snapped = d; else break; }
    return snapped ?? eventDates[0];
  }, [asof, eventDates]);
  const lastIdx = Math.max(0, eventDates.length - 1);
  const asofIdx = effAsof == null ? lastIdx : Math.max(0, eventDates.indexOf(effAsof));
  const stepAsof = (d: number) => {
    if (eventDates.length === 0) return;
    const i = clamp(asofIdx + d, 0, lastIdx);
    setAsof(i >= lastIdx ? null : eventDates[i]);
  };

  const maxActivity = useMemo(() => Math.max(1, ...activity.map((a) => a.count)), [activity]);

  const rulerLabels = useMemo(() => {
    if (eventDates.length === 0) return [];
    const a = isoToDate(eventDates[0]).getTime();
    const b = isoToDate(eventDates[eventDates.length - 1]).getTime();
    if (a === b) return [isoToDate(eventDates[0]).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })];
    return [0, 1 / 3, 2 / 3, 1].map((t) =>
      new Date(a + (b - a) * t).toLocaleDateString("en-US", { month: "short", day: "numeric" }));
  }, [eventDates]);

  // ————— focal closure (focus + 1 hop, plus expanded neighborhoods) —————
  const focusClosure = useMemo(() => {
    if (!focus || !nodeById[focus]) return null;
    const s = new Set<string>([focus]);
    for (const m of adj.get(focus) ?? []) s.add(m);
    let changed = true;
    while (changed) {
      changed = false;
      for (const ex of expanded) {
        if (!s.has(ex)) continue;
        for (const m of adj.get(ex) ?? []) if (!s.has(m)) { s.add(m); changed = true; }
      }
    }
    return s;
  }, [focus, expanded, adj, nodeById]);

  // ————— visibility filters —————
  const visibleNodeIds = useMemo(() => {
    const chipPass = (n: LNode): boolean => {
      switch (chip) {
        case "stale": return !!n.warn || (n.staleDays ?? 0) > 45; // match the server's tag-based stale count
        case "orphans": return isOrphan(n);
        case "unowned": return !n.owner;
        case "contradictions": return contraAny.has(n.id);
        case "untranslated": return untranslatedIds.has(n.id);
        default: return true;
      }
    };
    const s = new Set<string>();
    for (const n of nodes) {
      if (!srcOn(srcKey(n.source))) continue;
      if (statusFilter === "Warnings" && !n.warn) continue;
      if (effAsof != null && n.createdDate != null && n.createdDate > effAsof) continue;
      if (chip && !chipPass(n)) continue;
      if (scope === "focus" && focusClosure && !focusClosure.has(n.id)) continue;
      s.add(n.id);
    }
    return s;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, sourcesOn, statusFilter, effAsof, chip, scope, focusClosure, contraAny, untranslatedIds, adj]);

  const visibleEdges = useMemo(
    () =>
      edges.filter(
        (e) =>
          relsOn[e.rel] &&
          visibleNodeIds.has(e.from) &&
          visibleNodeIds.has(e.to) &&
          (effAsof == null || e.date == null || e.date <= effAsof),
      ),
    [edges, relsOn, visibleNodeIds, effAsof],
  );

  // ————— roll-up: collapse gh:<repo>:<kind> buckets into macro nodes —————
  // members ranked by edge degree desc then recency; open groups render the
  // first N pages of members (25/page) + a "+N more" stub, collapsed groups
  // render one macro node that all member edges re-point to.
  const rankedGroups = useMemo(() => {
    const m = new Map<string, LNode[]>();
    for (const n of nodes) {
      if (!n.group) continue;
      if (!m.has(n.group)) m.set(n.group, []);
      m.get(n.group)!.push(n);
    }
    for (const arr of m.values()) {
      arr.sort((a, b) =>
        ((adj.get(b.id)?.size ?? 0) - (adj.get(a.id)?.size ?? 0)) ||
        (b.date ?? "").localeCompare(a.date ?? ""));
    }
    return m;
  }, [nodes, adj]);

  const rollup = useMemo(() => {
    const renderedIds = new Set<string>(); // individual nodes on the canvas
    const memberToMacro = new Map<string, string>(); // collapsed member → macro id
    const macros: { id: string; gid: string; count: number; total: number; x: number; y: number; date?: string }[] = [];
    const stubs: { id: string; gid: string; rest: number; anchorId: string }[] = [];
    for (const n of nodes) if (!n.group && visibleNodeIds.has(n.id)) renderedIds.add(n.id);
    for (const [gid, ranked] of rankedGroups) {
      const vis = ranked.filter((m) => visibleNodeIds.has(m.id));
      if (vis.length === 0) continue; // nothing passes the filters → no macro either
      if (openGroups.has(gid)) {
        const shown = vis.slice(0, (groupPages[gid] ?? 1) * GROUP_PAGE_SIZE);
        for (const m of shown) renderedIds.add(m.id);
        if (vis.length > shown.length) {
          stubs.push({ id: `${STUB_PREFIX}${gid}`, gid, rest: vis.length - shown.length, anchorId: shown[shown.length - 1].id });
        }
      } else {
        const macroId = `${MACRO_PREFIX}${gid}`;
        for (const m of vis) memberToMacro.set(m.id, macroId);
        // macro takes the most-recent visible member's date + position
        const recent = vis.reduce((a, b) => ((a.date ?? "") >= (b.date ?? "") ? a : b));
        macros.push({ id: macroId, gid, count: vis.length, total: ranked.length, x: recent.x, y: recent.y, date: recent.date });
      }
    }
    return { renderedIds, memberToMacro, macros, stubs };
  }, [nodes, visibleNodeIds, rankedGroups, openGroups, groupPages]);

  // re-point member edges to macro nodes, dedupe per (from, to, rel) with an
  // aggregated count; drop edges fully inside one collapsed group and edges
  // to open-group members not paged in yet
  const renderEdges = useMemo(() => {
    const passthrough: LEdge[] = [];
    const agg = new Map<string, { from: string; to: string; rel: RelKey; count: number }>();
    for (const e of visibleEdges) {
      const from = rollup.memberToMacro.get(e.from) ?? (rollup.renderedIds.has(e.from) ? e.from : null);
      const to = rollup.memberToMacro.get(e.to) ?? (rollup.renderedIds.has(e.to) ? e.to : null);
      if (!from || !to || from === to) continue;
      if (from === e.from && to === e.to) { passthrough.push(e); continue; }
      const key = `${AGG_EDGE_PREFIX}${from}→${to}:${e.rel}`;
      const cur = agg.get(key);
      if (cur) cur.count += 1;
      else agg.set(key, { from, to, rel: e.rel, count: 1 });
    }
    return { passthrough, agg: [...agg.entries()].map(([id, a]) => ({ id, ...a })) };
  }, [visibleEdges, rollup]);

  const expandGroup = (gid: string) =>
    setOpenGroups((s) => (s.has(gid) ? s : new Set([...s, gid])));
  const collapseGroup = (gid: string) => {
    setOpenGroups((s) => { const next = new Set(s); next.delete(gid); return next; });
    setGroupPages((p) => { const { [gid]: _drop, ...rest } = p; return rest; });
  };
  // make a grouped member individually visible: open its group and page far
  // enough in for its rank (deep links / search / drawer rows on collapsed members)
  const revealNode = (id: string) => {
    const n = nodeById[id];
    if (!n?.group) return;
    const gid = n.group;
    expandGroup(gid);
    const idx = (rankedGroups.get(gid) ?? []).filter((m) => visibleNodeIds.has(m.id)).findIndex((m) => m.id === id);
    if (idx < 0) return;
    const need = Math.ceil((idx + 1) / GROUP_PAGE_SIZE);
    setGroupPages((p) => ((p[gid] ?? 1) >= need ? p : { ...p, [gid]: need }));
  };

  // ————— find path (BFS over visible edges) —————
  const pathHighlight = useMemo(() => {
    if (!pathMode || pathPicks.length !== 2) return null;
    const [a, b] = pathPicks;
    const adjL = new Map<string, { next: string; edge: LEdge }[]>();
    for (const e of visibleEdges) {
      if (!adjL.has(e.from)) adjL.set(e.from, []);
      if (!adjL.has(e.to)) adjL.set(e.to, []);
      adjL.get(e.from)!.push({ next: e.to, edge: e });
      adjL.get(e.to)!.push({ next: e.from, edge: e });
    }
    const prev = new Map<string, { node: string; edge: LEdge }>();
    const seen = new Set([a]);
    const queue = [a];
    while (queue.length) {
      const cur = queue.shift()!;
      if (cur === b) break;
      for (const { next, edge } of adjL.get(cur) ?? []) {
        if (seen.has(next)) continue;
        seen.add(next);
        prev.set(next, { node: cur, edge });
        queue.push(next);
      }
    }
    if (!seen.has(b)) return { nodes: new Set([a, b]), edges: new Set<LEdge>() };
    const nodeSet = new Set([b]);
    const edgeSet = new Set<LEdge>();
    let cur = b;
    while (cur !== a) {
      const p = prev.get(cur)!;
      edgeSet.add(p.edge);
      nodeSet.add(p.node);
      cur = p.node;
    }
    return { nodes: nodeSet, edges: edgeSet };
  }, [pathMode, pathPicks, visibleEdges]);

  // ————— trace closure (impact = downstream, provenance = upstream) —————
  const traceInfo = useMemo(() => {
    if (!trace || !nodeById[trace.nodeId]) return null;
    const origin = nodeById[trace.nodeId];
    const nodesSet = new Set([trace.nodeId]);
    const edgesSet = new Set<LEdge>();
    const rows: { id: string; edge: LEdge }[] = [];
    const queue = [trace.nodeId];
    while (queue.length) {
      const cur = queue.shift()!;
      for (const e of visibleEdges) {
        const nxt = trace.dir === "down" ? (e.from === cur ? e.to : null) : (e.to === cur ? e.from : null);
        if (!nxt) continue;
        edgesSet.add(e);
        if (!nodesSet.has(nxt)) { nodesSet.add(nxt); rows.push({ id: nxt, edge: e }); queue.push(nxt); }
      }
    }
    // provenance: upstream docs edited after the focal's last update
    const changedSince = new Set<string>();
    if (trace.dir === "up" && origin.date) {
      for (const id of nodesSet) {
        if (id !== trace.nodeId && (nodeById[id]?.date ?? "") > origin.date) changedSince.add(id);
      }
    }
    const groups = new Map<RelKey, { id: string; edge: LEdge }[]>();
    for (const r of rows) {
      if (!groups.has(r.edge.rel)) groups.set(r.edge.rel, []);
      groups.get(r.edge.rel)!.push(r);
    }
    return { origin, nodes: nodesSet, edges: edgesSet, rows, groups, changedSince };
  }, [trace, visibleEdges, nodeById]);

  // one dimming mechanism: path > trace > focus-in-everything
  const focusDim = useMemo(() => {
    if (scope !== "all" || !focusClosure) return null;
    return {
      nodes: focusClosure,
      edges: new Set(visibleEdges.filter((e) => focusClosure.has(e.from) && focusClosure.has(e.to))),
    };
  }, [scope, focusClosure, visibleEdges]);
  const highlight = pathHighlight ?? (traceInfo ? { nodes: traceInfo.nodes, edges: traceInfo.edges } : focusDim);

  // owners present, for the ownership-lens legend
  const ownersLegend = useMemo(() => {
    if (lens !== "owner") return [];
    const s = new Set<string>();
    for (const n of nodes) if (visibleNodeIds.has(n.id) && n.owner) s.add(n.owner);
    return [...s].slice(0, 6);
  }, [lens, nodes, visibleNodeIds]);

  // ————— cytoscape —————
  const cyRef = useRef<Core | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const rulerRef = useRef<HTMLDivElement | null>(null);
  const pendingFly = useRef<string | null>(null);
  const lastBgTap = useRef(0);

  // time-ruler labels track the canvas viewport (timeline layout only);
  // server auto-layout maps time into the 0.24..0.92 x band
  const updateRuler = (cy: Core) => {
    const el = rulerRef.current;
    if (!el) return;
    const spans = el.children;
    const n = spans.length;
    for (let i = 0; i < n; i++) {
      const t = n === 1 ? 0.5 : i / (n - 1);
      const modelX = (0.24 + 0.68 * t) * TL_W;
      const x = modelX * cy.zoom() + cy.pan().x;
      (spans[i] as HTMLElement).style.left = `${x}px`;
    }
  };

  // ————— pin persistence (timeline layout; normalized to the virtual space) —————
  const persistPin = async (id: string, x: number, y: number) => {
    setPinnedLocal((p) => ({ ...p, [id]: true }));
    const n = nodeById[id];
    if (n?.docId != null) {
      await gql(
        `mutation($id: Int!, $x: Float!, $y: Float!) { pinNode(documentId: $id, x: $x, y: $y) }`,
        { id: n.docId, x, y },
      );
    }
  };
  const pinCurrent = (id: string) => {
    const cn = cyRef.current?.getElementById(id);
    if (!cn || cn.empty()) return;
    const pos = cn.position();
    persistPin(id, pos.x / TL_W, pos.y / TL_H);
  };
  const unpin = async (id: string) => {
    setPinnedLocal((p) => ({ ...p, [id]: false }));
    const n = nodeById[id];
    if (n?.docId != null) {
      await gql(`mutation($id: Int!) { unpinNode(documentId: $id) }`, { id: n.docId });
      refetchGraph(); // refetch → auto position restored
    }
  };

  // latest handlers/data, so cytoscape event bindings never go stale
  // (refreshed after every render — events always fire after the effect ran)
  const hRef = useRef<any>({});
  useEffect(() => {
    hRef.current = {
      onNodeTap: (id: string) => {
        if (id.startsWith(MACRO_PREFIX)) {
          // tapping a macro node expands it in place + shows the group summary
          const gid = id.slice(MACRO_PREFIX.length);
          expandGroup(gid);
          setSel({ kind: "node", id });
          setAssertOpen(false);
          return;
        }
        if (id.startsWith(STUB_PREFIX)) {
          // "+N more" stub pages in the next 25 members
          const gid = id.slice(STUB_PREFIX.length);
          setGroupPages((p) => ({ ...p, [gid]: (p[gid] ?? 1) + 1 }));
          return;
        }
        activateNode(id);
      },
      onEdgeTap: (id: string) => {
        if (id.startsWith(AGG_EDGE_PREFIX)) return; // aggregated edges have no single doc pair
        setSel({ kind: "edge", id });
        setAssertOpen(false);
      },
      onBgTap: () => {
        const now = Date.now();
        if (now - lastBgTap.current < 350) cyRef.current?.fit(undefined, 40);
        lastBgTap.current = now;
        setSel(null);
        setAssertOpen(false);
      },
      onDragFree: (id: string, pos: { x: number; y: number }) => {
        if (layout !== "timeline") return; // flow-layout drags are session-only
        if (id.startsWith(MACRO_PREFIX) || id.startsWith(STUB_PREFIX)) return; // macro nodes are never pinnable
        persistPin(id, pos.x / TL_W, pos.y / TL_H);
      },
    };
  });

  useEffect(() => {
    if (!containerRef.current) return; // guards strict-mode double init: destroy in cleanup
    const cy = cytoscape({
      container: containerRef.current,
      style: CY_STYLE,
      elements: [],
      minZoom: 0.3,
      maxZoom: 2.5,
      wheelSensitivity: 0.4,
      boxSelectionEnabled: false,
      autounselectify: true,
    });
    cyRef.current = cy;
    (window as any).__lineageCy = cy; // debug/testing handle

    cy.on("tap", "node", (ev) => hRef.current.onNodeTap(ev.target.id()));
    cy.on("tap", "edge", (ev) => hRef.current.onEdgeTap(ev.target.id()));
    cy.on("tap", (ev) => { if (ev.target === cy) hRef.current.onBgTap(); });
    cy.on("dragfree", "node", (ev) => hRef.current.onDragFree(ev.target.id(), { ...ev.target.position() }));
    cy.on("mouseover", "node", (ev) => {
      const p = ev.target.renderedPosition();
      setTip({ id: ev.target.id(), x: p.x, y: p.y, w: cy.width() });
    });
    cy.on("mouseout", "node", () => setTip(null));
    cy.on("grab", "node", () => setTip(null));
    cy.on("zoom", () => setZoomPct(Math.round(cy.zoom() * 100)));
    cy.on("pan zoom", () => { setTip(null); updateRuler(cy); });

    return () => { cy.destroy(); cyRef.current = null; };
  }, []);

  // build cytoscape elements from the mapped api data (individual nodes that
  // survived the roll-up, plus macro/stub nodes and re-pointed edges)
  const elements = useMemo(() => {
    const ns: any[] = [];
    for (const n of nodes) {
      if (!rollup.renderedIds.has(n.id)) continue;
      ns.push({
        group: "nodes" as const,
        data: {
          id: n.id,
          label: `${SOURCE_GLYPH[srcKey(n.source)] ?? "▢"}  ${n.title}`,
          ring: n.warn ? "#c8502e" : "#cfc4a8",
          uc: SOURCE_ACCENT[srcKey(n.source)] ?? "#6b6353",
        },
        position: { x: n.x * TL_W, y: n.y * TL_H },
        classes: n.warn ? "warn" : "",
      });
      // "+N more" paging stub rides directly after its group's last member so
      // the orphan grid (insertion-ordered) keeps it beside the group
      const stub = rollup.stubs.find((s) => s.anchorId === n.id);
      if (stub) {
        ns.push({
          group: "nodes" as const,
          data: { id: stub.id, label: `+${stub.rest} more`, ring: "#cfc4a8", uc: SOURCE_ACCENT.github },
          position: { x: n.x * TL_W + 40, y: n.y * TL_H + 40 },
          classes: "stub",
        });
      }
    }
    for (const m of rollup.macros) {
      const { repo, kind } = groupParts(m.gid);
      ns.push({
        group: "nodes" as const,
        data: {
          id: m.id,
          label: `${SOURCE_GLYPH.github}  ${m.count} ${groupKindWord(kind, m.count)}\n${repo}`,
          ring: "#a89a7c",
          uc: SOURCE_ACCENT.github,
        },
        position: { x: m.x * TL_W, y: m.y * TL_H },
        classes: "macro",
      });
    }
    const es = [
      ...renderEdges.passthrough.map((e) => ({
        group: "edges" as const,
        data: { id: edgeKey(e), source: e.from, target: e.to, color: REL[e.rel].color },
        classes: e.llm || e.dashed || REL[e.rel].dash ? "dashed" : "",
      })),
      ...renderEdges.agg.map((a) => ({
        group: "edges" as const,
        data: {
          id: a.id, source: a.from, target: a.to, color: REL[a.rel].color,
          glabel: a.count > 1 ? `${a.rel} ×${a.count}` : a.rel,
        },
        classes: REL[a.rel].dash ? "dashed" : "",
      })),
    ];
    return [...ns, ...es];
  }, [nodes, rollup, renderEdges]);

  // recreate elements + run the layout on every data / filter / scope change,
  // then fit so nothing is ever off-screen
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.batch(() => {
      cy.elements().remove();
      // clone positions: cytoscape mutates position objects in place when a
      // layout runs, which would corrupt the memoized preset coordinates
      cy.add(elements.map((el: any) => (el.position ? { ...el, position: { ...el.position } } : el)) as any);
    });
    if (layout === "flow") {
      // dagre stacks disconnected nodes into one tall first rank, which
      // crushes the fit zoom — run dagre on the linked subgraph and grid the
      // orphans in compact rows beneath it instead
      const orphans = cy.nodes().filter((n) => n.degree(false) === 0);
      const linked = cy.nodes().not(orphans);
      if (linked.length > 0) {
        linked.union(linked.edgesWith(linked))
          .layout({ name: "dagre", rankDir: "LR", nodeSep: 18, rankSep: 80, edgeSep: 10, animate: false } as any)
          .run();
      }
      if (orphans.length > 0) {
        const bb = linked.length > 0 ? linked.boundingBox({}) : { x1: 0, y2: -80, w: 900 };
        const cols = Math.max(3, Math.ceil(Math.sqrt(orphans.length * 2.6)));
        orphans.forEach((n, i) => {
          n.position({ x: bb.x1 + 85 + (i % cols) * 200, y: bb.y2 + 85 + Math.floor(i / cols) * 64 });
        });
      }
    }
    // timeline layout = preset positions already set at add-time
    if (cy.elements().length > 0) cy.fit(undefined, 40);
    setZoomPct(Math.round(cy.zoom() * 100));
    updateRuler(cy);
  }, [elements, layout]);

  // decorations: lens colors, selection, focal ring, path picks, dimming —
  // applied without rebuilding elements (no re-layout, no re-fit)
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.batch(() => {
      cy.nodes().forEach((cn) => {
        const n = nodeById[cn.id()];
        if (!n) {
          // synthetic roll-up nodes: macro stays lit if any member is in the
          // highlight closure; stubs follow the general dimming
          if (cn.hasClass("macro")) {
            const members = rankedGroups.get(cn.id().slice(MACRO_PREFIX.length)) ?? [];
            cn.toggleClass("dim", highlight != null && !members.some((m) => highlight.nodes.has(m.id)));
            cn.toggleClass("sel", panelNode === cn.id());
          } else if (cn.hasClass("stub")) {
            cn.toggleClass("dim", highlight != null);
          }
          return;
        }
        let ring = n.warn ? "#c8502e" : "#cfc4a8";
        let uc = SOURCE_ACCENT[srcKey(n.source)] ?? "#6b6353";
        switch (lens) {
          case "stale": {
            ring = staleColor(n.staleDays ?? 0);
            uc = ring;
            break;
          }
          case "owner": {
            ring = n.owner ? ownerColor(n.owner) : "#8a7f68";
            uc = ring;
            break;
          }
          case "health": {
            const bad = n.warn || isOrphan(n) || contraInbound.has(n.id) || lagIds.has(n.id);
            ring = bad ? "#c0392b" : "#43663c";
            uc = ring;
            break;
          }
        }
        cn.data({ ring, uc });
        cn.toggleClass("focal", n.id === focus);
        cn.toggleClass("sel", !pathMode && panelNode === n.id);
        cn.toggleClass("pick", pathMode && pathPicks.includes(n.id));
        cn.toggleClass("future", effAsof != null && n.date != null && n.date > effAsof);
        cn.toggleClass("changed", traceInfo?.changedSince.has(n.id) ?? false);
        cn.toggleClass("dim", highlight != null && !highlight.nodes.has(n.id));
      });
      cy.edges().forEach((ce) => {
        const e = edgeByKey[ce.id()];
        ce.toggleClass("sel", sel?.kind === "edge" && sel.id === ce.id());
        ce.toggleClass("dim", highlight != null && (!e || !highlight.edges.has(e)));
      });
    });
  }, [elements, layout, lens, focus, panelNode, pathMode, pathPicks, effAsof, traceInfo, highlight, sel, nodeById, edgeByKey, contraInbound, lagIds]); // eslint-disable-line react-hooks/exhaustive-deps

  // search / top-cited fly-to, after any pending rebuild has fit the graph
  useEffect(() => {
    const cy = cyRef.current;
    const id = pendingFly.current;
    if (!cy || !id) return;
    pendingFly.current = null;
    const n = cy.getElementById(id);
    if (n.nonempty()) {
      cy.stop();
      cy.animate({ center: { eles: n }, zoom: 1.2 }, { duration: 300 });
    }
  });

  const zoomBy = (f: number) => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.zoom({ level: cy.zoom() * f, renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } });
  };
  const fit = () => cyRef.current?.fit(undefined, 40);

  // ————— search (typeahead over the loaded graph) —————
  const searchResults = useMemo(() => {
    const t = q.trim().toLowerCase();
    if (!t) return [];
    return nodes
      .filter((n) =>
        n.title.toLowerCase().includes(t) ||
        (n.owner ?? "").toLowerCase().includes(t) ||
        n.source.toLowerCase().includes(t) ||
        (n.tags ?? []).some((tag) => tag.toLowerCase().includes(t)))
      .sort((a, b) => (inboundOf(b) - inboundOf(a)) || (b.date ?? "").localeCompare(a.date ?? ""))
      .slice(0, 7);
  }, [q, nodes, inboundOf]);
  const pickSearch = (id: string) => {
    setQ("");
    revealNode(id); // grouped member → expand its roll-up so the fly-to lands
    focusOn(id);
    setSel({ kind: "node", id });
    setAssertOpen(false);
    pendingFly.current = id; // fly there once the graph has settled
  };

  // ————— deep links: sync view state to the URL —————
  const relsKey = (Object.keys(REL) as RelKey[]).filter((k) => relsOn[k]).join(",");
  useEffect(() => {
    const p = new URLSearchParams();
    if (focus) p.set("focus", focus);
    if (lens !== "source") p.set("lens", lens);
    if (asof) p.set("asof", asof);
    if (relsKey !== Object.keys(REL).join(",")) p.set("rels", relsKey);
    if (scope !== "focus") p.set("scope", scope);
    if (chip) p.set("chip", chip);
    if (openGroups.size > 0) p.set("open", [...openGroups].join(","));
    const qs = p.toString();
    window.history.replaceState(null, "", qs ? `?${qs}` : window.location.pathname);
  }, [focus, lens, asof, relsKey, scope, chip, openGroups]);

  // deep link ?focus= on a collapsed member: auto-expand its group once the
  // graph has loaded, so the focused node is actually on the canvas
  useEffect(() => {
    if (focus && nodeById[focus]?.group) revealNode(focus);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus, nodes.length]);

  // ————— saved views —————
  const saveView = async () => {
    const name = window.prompt("Name this view:");
    if (!name?.trim()) return;
    const state: ViewState = {
      focus, lens, asof, scope, chip,
      rels: (Object.keys(REL) as RelKey[]).filter((k) => relsOn[k]),
      open: [...openGroups],
    };
    const d = await gql(
      `mutation($name: String!, $state: String!) { saveGraphView(name: $name, state: $state) }`,
      { name: name.trim(), state: JSON.stringify(state) },
    );
    if (d) viewsQ.refetch();
  };
  const applyView = (v: GraphView) => {
    try {
      const s = JSON.parse(v.state) as Partial<ViewState>;
      if (s.focus !== undefined) { setFocusRaw(s.focus); setExpanded(new Set()); }
      if (s.lens && LENSES.some((l) => l.key === s.lens)) setLens(s.lens);
      if (s.asof !== undefined) setAsof(s.asof);
      if (Array.isArray(s.rels)) {
        setRelsOn(Object.fromEntries((Object.keys(REL) as RelKey[]).map((k) => [k, s.rels!.includes(k)])) as Record<RelKey, boolean>);
      }
      if (s.scope === "focus" || s.scope === "all") setScope(s.scope);
      if (s.chip !== undefined) setChip(s.chip);
      if (Array.isArray(s.open)) { setOpenGroups(new Set(s.open)); setGroupPages({}); }
    } catch { /* malformed view state — ignore */ }
  };

  // ————— ecosystem strip —————
  const chipClick = (key: string) => {
    if (chip === key) { setChip(null); return; }
    setChip(key);
    if (key === "stale") setLens("stale");
  };
  const focusTopCited = (docId: number) => {
    const n = nodes.find((x) => x.docId === docId);
    if (n) { revealNode(n.id); focusOn(n.id); setSel({ kind: "node", id: n.id }); setAssertOpen(false); pendingFly.current = n.id; }
  };

  useEffect(() => {
    if (!pathMode) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { setPathMode(false); setPathPicks([]); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pathMode]);

  // Esc closes the drawer, then the assertion drawer, then the trace
  useEffect(() => {
    if (!sel && !assertOpen && !trace) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (sel) setSel(null);
      else if (assertOpen) setAssertOpen(false);
      else if (trace) setTrace(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [sel, assertOpen, trace]);

  // if filters/scrubber hide the selection, drop the drawer/trace with it
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (!sel) return;
    if (sel.kind === "node") {
      if (sel.id.startsWith(MACRO_PREFIX)) {
        // group drawer survives expansion; drop it only if the group vanished
        if (!rankedGroups.has(sel.id.slice(MACRO_PREFIX.length))) setSel(null);
      } else if (!visibleNodeIds.has(sel.id)) setSel(null);
    }
    if (sel.kind === "edge" && !visibleEdges.some((e) => edgeKey(e) === sel.id)) setSel(null);
  }, [sel, visibleNodeIds, visibleEdges, rankedGroups]);
  useEffect(() => {
    if (trace && !visibleNodeIds.has(trace.nodeId)) setTrace(null);
  }, [trace, visibleNodeIds]);
  /* eslint-enable react-hooks/set-state-in-effect */

  // ————— actions —————
  const deriveLinks = async () => {
    if (deriving) return;
    setDeriving(true);
    await gql(`mutation { deriveLinks }`);
    setDeriving(false);
    refetchGraph(); // refetch nodes+edges; new LLM edges appear dashed
  };

  const analyze = async () => {
    if (analyzing || !claim.trim()) return;
    setAnalyzing(true);
    setTaskState("idle");
    setSeverityFilter(null);
    const d: any = await gql(
      `mutation($claim: String!) { impactAnalysis(claim: $claim) { claim summary docs { title source severity reason } } }`,
      { claim: claim.trim() },
    );
    if (d?.impactAnalysis) {
      setResult(d.impactAnalysis);
      setAnalyzedAt(new Date().toLocaleString("en-US", { month: "long", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" }));
    }
    setAnalyzing(false);
  };

  const createTasks = async () => {
    if (!result || taskState === "creating") return;
    setTaskState("creating");
    let failed = 0;
    for (const doc of result.docs) {
      const sev = SEVERITY_TASK[doc.severity] ?? SEVERITY_TASK.minor;
      const d = await gql(
        `mutation($title: String!, $kind: String!, $kindLabel: String!) { createTask(title: $title, kind: $kind, kindLabel: $kindLabel) }`,
        { title: `Update: ${doc.title}`, kind: sev.kind, kindLabel: sev.kindLabel },
      );
      if (!d) failed++;
    }
    // honest outcome: any failure surfaces as an error state, never a fake "done"
    setTaskState(failed > 0 ? "error" : "done");
  };

  const exportReport = () => {
    if (!result) return; // nothing analyzed yet — no demo payloads
    const payload = { ...result, generatedAt: new Date().toISOString() };
    downloadText("impact-analysis.json", JSON.stringify(payload, null, 2), "application/json");
  };

  // ————— drawer-side derived values —————
  const selEdge = sel?.kind === "edge" ? edgeByKey[sel.id] : undefined;
  const hiddenNeighbors = useMemo(() => {
    if (!panelNode || scope !== "focus" || !focusClosure) return 0;
    return [...(adj.get(panelNode) ?? [])].filter((x) => !visibleNodeIds.has(x) && nodeById[x]).length;
  }, [panelNode, scope, focusClosure, adj, visibleNodeIds, nodeById]);
  const tipNode = tip ? nodeById[tip.id] : undefined;

  return (
    <div className="lg-page">
      <h2 className="page-title lg-title">
        Product lineage
        <Button className="lg-title-btn" onClick={() => navigate("/knowledge")}>
          <Ic.Doc size={16} /> Authentication API <Ic.Chev size={14} />
        </Button>
      </h2>

      {/* one full-height instrument: the graph card */}
      <div className="card lg-card">
        <Toolbar
          q={q}
          onQ={setQ}
          searchResults={searchResults}
          inboundOf={inboundOf}
          onPickSearch={pickSearch}
          sourceGroups={sourceGroups}
          srcOn={srcOn}
          onToggleSource={(s) => setSourcesOn((m) => ({ ...m, [s]: !srcOn(s) }))}
          relsOn={relsOn}
          onToggleRel={(k) => setRelsOn((m) => ({ ...m, [k]: !m[k] }))}
          statusFilter={statusFilter}
          onStatusFilter={setStatusFilter}
          lens={lens}
          onLens={setLens}
          layout={layout}
          onLayout={setLayout}
          scope={scope}
          onToggleScope={() => setScope((s) => (s === "focus" ? "all" : "focus"))}
          views={views}
          onApplyView={applyView}
          onSaveView={saveView}
          deriving={deriving}
          onDerive={deriveLinks}
          assertOpen={assertOpen}
          onToggleAssert={() => { setAssertOpen((v) => !v); setSel(null); }}
          pathMode={pathMode}
          pathPickCount={pathPicks.length}
          onTogglePath={() => { setPathMode((v) => !v); setPathPicks([]); setSel(null); }}
          zoomPct={zoomPct}
          onZoomBy={zoomBy}
          onFit={fit}
        />

        {/* ecosystem strip — hidden when graphStats is unavailable */}
        {stats && (
          <div className="eco-strip">
            <Chip>{stats.docs} documents</Chip>
            {([
              ["stale", `${stats.stale} stale`],
              ["orphans", `${stats.orphans} orphans`],
              ["untranslated", `${stats.untranslated} untranslated`],
              ["unowned", `${stats.unowned} unowned`],
              ["contradictions", `${stats.contradictions} contradiction${stats.contradictions === 1 ? "" : "s"}`],
            ] as [string, string][]).map(([k, label]) => (
              <Chip key={k} selected={chip === k} aria-pressed={chip === k} onClick={() => chipClick(k)}>
                {label}
              </Chip>
            ))}
            {stats.topCited[0] && (
              <Chip onClick={() => focusTopCited(stats.topCited[0].docId)} title="Focus the most-cited document">
                <span className="lg-ellip">Top cited: {stats.topCited[0].title} ({stats.topCited[0].inbound})</span>
              </Chip>
            )}
          </div>
        )}

        {/* context chips + lens legend */}
        <div className="lg-legend">
          {focus && nodeById[focus] && (
            <Chip tone="terra" onClick={() => focusOn(null)} title="Clear focus">
              <span className="lg-ellip">✕ Focus: {nodeById[focus].title}</span>
            </Chip>
          )}
          {expanded.size > 0 && (
            <Chip tone="terra" onClick={() => setExpanded(new Set())}>Collapse to focus</Chip>
          )}
          {[...openGroups].filter((gid) => rankedGroups.has(gid)).map((gid) => {
            const { repo, kind } = groupParts(gid);
            const total = rankedGroups.get(gid)!.length;
            return (
              <Chip key={gid} tone="terra" onClick={() => collapseGroup(gid)} title={`Collapse ${repo} ${kind} back to one node`}>
                ⊖ {total} {groupKindWord(kind, total)} · {repo}
              </Chip>
            );
          })}
          {chip && (
            <Chip tone="terra" onClick={() => setChip(null)} title="Clear filter">✕ Filter: {chip}</Chip>
          )}
          {lens === "source" && Object.values(REL).map((r) => (
            <span key={r.label} className="row" style={{ gap: 7 }}>
              <svg width="26" height="4"><line x1="0" y1="2" x2="26" y2="2" stroke={r.color} strokeWidth="2" strokeDasharray={r.dash} /></svg>
              {r.label}
            </span>
          ))}
          {lens === "stale" && ([["≤ 14 days", "#43663c"], ["≤ 45 days", "#c8973a"], ["> 45 days", "#c0392b"]] as const).map(([label, c]) => (
            <span key={label} className="row" style={{ gap: 6 }}>
              <span className="lg-dot" style={{ background: c }} /> {label}
            </span>
          ))}
          {lens === "owner" && (
            <>
              {ownersLegend.map((o) => (
                <span key={o} className="row" style={{ gap: 6 }}>
                  <span className="lg-dot" style={{ background: ownerColor(o) }} /> {o}
                </span>
              ))}
              <span className="row" style={{ gap: 6 }}>
                <span className="lg-dot" style={{ background: "#8a7f68" }} /> Unowned
              </span>
            </>
          )}
          {lens === "health" && ([["Healthy", "#43663c"], ["Warn / orphan / contradicted / lag", "#c0392b"]] as const).map(([label, c]) => (
            <span key={label} className="row" style={{ gap: 6 }}>
              <span className="lg-dot" style={{ background: c }} /> {label}
            </span>
          ))}
        </div>

        {/* canvas — cytoscape fills all remaining height; paper grid behind */}
        <div className="lineage__canvas lg-canvas" ref={canvasRef}>
          <div ref={containerRef} className="lg-cy" />

          {/* honest states — the cytoscape container stays mounted underneath */}
          {graphQ.loading && (
            <div className="lg-canvas-state lg-canvas-state--loading">
              <Spinner size="md" label="Loading lineage graph" />
            </div>
          )}
          {graphQ.error && !graphQ.data && (
            <div className="lg-canvas-state">
              <EmptyState>API offline — the lineage graph is unavailable.</EmptyState>
            </div>
          )}

          {/* hover tooltip: meta line lives here (labels stay one line of type) */}
          {tip && tipNode && (
            <div className="lg-tip" style={{ left: clamp(tip.x, 70, tip.w - 70), top: Math.max(8, tip.y - 52) }}>
              <b>{tipNode.title}</b>
              <span>{[tipNode.meta, SOURCE_LABELS[srcKey(tipNode.source)], tipNode.owner || null, tipNode.staleDays != null ? `${tipNode.staleDays}d stale` : null].filter(Boolean).join(" · ")}</span>
            </div>
          )}

          {/* time ruler — meaningful in the timeline layout (x = time) */}
          {layout === "timeline" && rulerLabels.length > 0 && (
            <div className="lg-ruler" ref={rulerRef} aria-hidden>
              {rulerLabels.map((label, i) => <span key={i}>{label}</span>)}
            </div>
          )}

          {/* trace summary — bottom-left chip row; the grouped list lives in the drawer */}
          {traceInfo && trace && (
            <div className="lg-trace">
              <b className="lg-trace__title">
                {trace.dir === "down" ? "Impact of" : "Provenance of"} {traceInfo.origin.title}
              </b>
              <Chip>{traceInfo.rows.length} docs</Chip>
              {[...traceInfo.groups.entries()].map(([rel, rows]) => (
                <Chip key={rel} className={`lg-rel--${rel}`}>
                  {REL[rel].label} {rows.length}
                </Chip>
              ))}
              {traceInfo.changedSince.size > 0 && <span className="lchanged">{traceInfo.changedSince.size} changed since</span>}
              <Button compact onClick={() => setTrace(null)}>Clear</Button>
            </div>
          )}
        </div>

        {/* scrubber — snaps to real event dates; track shows activity density */}
        <Scrubber
          eventDates={eventDates}
          activity={activity}
          maxActivity={maxActivity}
          asofIdx={asofIdx}
          lastIdx={lastIdx}
          effAsof={effAsof}
          onStep={stepAsof}
          onSetAsof={setAsof}
        />
      </div>

      {/* ————— right drawers (product-standard fl-drawer pattern; no backdrop —
          the graph stays interactive, clicking another node retargets) ————— */}

      {panelNode?.startsWith(MACRO_PREFIX) && rankedGroups.has(panelNode.slice(MACRO_PREFIX.length)) && (() => {
        const gid = panelNode.slice(MACRO_PREFIX.length);
        return (
          <GroupDrawer
            groupId={gid}
            members={rankedGroups.get(gid)!}
            visibleCount={rankedGroups.get(gid)!.filter((m) => visibleNodeIds.has(m.id)).length}
            degreeOf={(id) => adj.get(id)?.size ?? 0}
            isOpen={openGroups.has(gid)}
            onExpand={() => expandGroup(gid)}
            onCollapse={() => collapseGroup(gid)}
            onSelectMember={(id) => openNode(id)}
            onClose={() => setSel(null)}
          />
        );
      })()}

      {panelNode && nodeById[panelNode] && (
        <NodeDrawer
          node={nodeById[panelNode]}
          nodesById={nodeById}
          edges={edges}
          pinned={pinnedLocal[panelNode] ?? nodeById[panelNode].pinned ?? false}
          layout={layout}
          hiddenNeighbors={hiddenNeighbors}
          groupChip={nodeById[panelNode].group && openGroups.has(nodeById[panelNode].group)
            ? {
                label: (() => { const { repo, kind } = groupParts(nodeById[panelNode].group); return `${repo} ${kind}`; })(),
                onCollapse: () => collapseGroup(nodeById[panelNode].group),
              }
            : null}
          traceRows={trace && traceInfo && trace.nodeId === panelNode
            ? { dir: trace.dir, groups: traceInfo.groups, changedSince: traceInfo.changedSince }
            : null}
          onSelect={(id) => openNode(id)}
          onFocus={(id) => { focusOn(id); setSel({ kind: "node", id }); }}
          onTrace={(dir) => setTrace({ nodeId: panelNode, dir })}
          onExpand={() => setExpanded((prev) => new Set([...prev, panelNode]))}
          onPin={() => pinCurrent(panelNode)}
          onUnpin={() => unpin(panelNode)}
          onClose={() => setSel(null)}
        />
      )}

      {selEdge && nodeById[selEdge.from] && nodeById[selEdge.to] && (
        <EdgeDrawer
          edge={selEdge}
          from={nodeById[selEdge.from]}
          to={nodeById[selEdge.to]}
          onSelect={(id) => openNode(id)}
          onClose={() => setSel(null)}
        />
      )}

      {/* assertion / impact-analysis drawer (same shell) */}
      {assertOpen && (
        <AssertDrawer
          claim={claim}
          onClaim={setClaim}
          analyzing={analyzing}
          onAnalyze={analyze}
          result={result}
          analyzedAt={analyzedAt}
          taskState={taskState}
          onCreateTasks={createTasks}
          onExport={exportReport}
          severityFilter={severityFilter}
          onSeverityFilter={(sev) => setSeverityFilter((s) => (s === sev ? null : sev))}
          onClose={() => setAssertOpen(false)}
        />
      )}
    </div>
  );
}
