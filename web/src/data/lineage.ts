/* Lineage adapter — the whole graph, its edges, and the scrubber's timeline.
 *
 * `LNode`/`LEdge` are plain JSON on both sides, so this is close to a straight
 * rename: the server already does the auto-layout, the roll-up classification
 * and the staleness arithmetic. */

import type { LineageData } from "@mari-design/components/pages/LineagePage";
import type { LEdge, LNode } from "@mari-design/components/features/LineageDataModel";
import { useQuery } from "../lib/api";
import type { PageData } from "./types";

/* ── query ──────────────────────────────────────────────────────────────── */

const QUERY = `{
  lineage {
    id docId source title meta icon x y pinned date createdDate
    warn owner tags staleDays orphan inbound outbound docKind group
  }
  lineageEdges { id fromId toId kind date meta }
  graphStats { activity { date count } }
}`;

type Res = {
  lineage: {
    id: string; docId: number; source: string; title: string; meta: string; icon: string;
    x: number; y: number; pinned: boolean; date: string; createdDate: string; warn: boolean;
    owner: string; tags: string[]; staleDays: number; orphan: boolean;
    inbound: number; outbound: number; docKind: string; group: string;
  }[];
  lineageEdges: {
    id: number; fromId: string; toId: string; kind: string; date: string;
    meta: { derived?: string; note?: string; evidence?: string; status?: string } | null;
  }[];
  graphStats: { activity: { date: string; count: number }[] } | null;
};

/* ── mapping helpers ────────────────────────────────────────────────────── */

/* The graph draws seven node shapes. `classify_node` and this build of the
   library ship separately, so a kind from a newer classifier renders as a
   blank glyph — a plain page is the honest fallback. */
const DOC_KINDS = new Set<LNode["docKind"]>([
  "page", "commit", "pr", "issue", "answer", "decision", "seed",
]);

/* edges.rel is an open column; the graph has a color, a dash pattern, a legend
   entry and a direction label for exactly six relations. Anything else is
   dropped rather than drawn as a relation it is not. */
const REL: Record<string, LEdge["rel"]> = {
  references: "references",
  reference: "references",
  links_to: "references",
  discussed: "discussed",
  derived: "derived",
  translates: "translates",
  contradicts: "contradicts",
  similar: "similar",
};

export function mapNodes(res: Res): LNode[] {
  return (res.lineage ?? [])
    .filter((n) => DOC_KINDS.has(n.docKind as LNode["docKind"]))
    .map<LNode>((n) => ({
      id: n.id,
      docId: n.docId,
      source: n.source,
      title: n.title,
      meta: n.meta,
      icon: n.icon,
      docKind: n.docKind as LNode["docKind"],
      group: n.group,
      x: n.x,
      y: n.y,
      date: n.date,
      createdDate: n.createdDate,
      pinned: n.pinned,
      warn: n.warn,
      owner: n.owner,
      tags: n.tags ?? [],
      staleDays: n.staleDays,
      orphan: n.orphan,
      inbound: n.inbound,
      outbound: n.outbound,
      // macro/count/repo describe a roll-up the server does not fold for us:
      // it returns every node plus its `group`, and the graph rolls up.
    }));
}

export function mapEdges(res: Res, nodes: LNode[]): LEdge[] {
  const known = new Set(nodes.map((n) => n.id));
  return (res.lineageEdges ?? [])
    .filter((e) => REL[e.kind] && known.has(e.fromId) && known.has(e.toId))
    .map<LEdge>((e) => ({
      id: String(e.id),
      from: e.fromId,
      to: e.toId,
      rel: REL[e.kind],
      date: e.date,
      // `derived: "llm"` is how links.py records a machine-proposed edge; the
      // graph draws those dashed and badges them.
      llm: e.meta?.derived === "llm",
      meta: e.meta ?? undefined,
    }));
}

/* ── mapper ─────────────────────────────────────────────────────────────── */

/** A workspace with no graph at all. Every drawer closed, the scrubber live. */
export const EMPTY: LineageData = {
  nodes: [], edges: [], dates: [], activity: [],
  lens: "source", layout: "flow",
  focalId: null, trace: null, asOf: null, search: null, drawer: null,
  crumbs: null, extras: null, action: "",
};

export function buildLineage(res: Res | null): LineageData {
  if (!res) return EMPTY;
  const nodes = mapNodes(res);
  // The scrubber snaps to the dates something actually happened on, which is
  // the same series that draws its density track.
  const activity = (res.graphStats?.activity ?? []).filter((a) => a.date);
  return {
    nodes,
    edges: mapEdges(res, nodes),
    dates: activity.map((a) => a.date),
    activity,
    // Lens, layout, focus, trace, as-of and the open drawer are all view state
    // the instrument owns. A freshly opened graph is the live one, whole,
    // nothing selected — the app does not have routes for the rest yet.
    lens: "source",
    layout: "flow",
    focalId: null,
    trace: null,
    asOf: null,
    search: null,
    drawer: null,
    crumbs: null,
    extras: null,
    action: "",
  };
}

/* ── adapter ────────────────────────────────────────────────────────────── */

export function useLineage(): PageData<LineageData> {
  const q = useQuery<LineageData>(QUERY, { map: buildLineage });
  return {
    data: q.data ?? EMPTY,
    loading: q.loading,
    error: q.error ? (q.errorText ?? "The lineage graph is temporarily unavailable.") : null,
  };
}
