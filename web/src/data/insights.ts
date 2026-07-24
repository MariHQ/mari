/* Insights adapter. Four root fields feed the widget block; freshness feeds
 * its own card and is nullable on the page, so the two are queried together
 * but mapped apart. */

import type { InsightsData, InsightsWidgetData } from "@mari-design/components/pages/InsightsPage";
import type { InsightStat, ReadRow, GlossRow, InsightsActivity } from "@mari-design/components/features/InsightsWidgets";
import type { Freshness } from "@mari-design/components/features/InsightsFreshnessChart";
import { useQuery } from "../lib/api";
import type { PageData } from "./types";

const QUERY = `{
  insightStats { searches answersServed driftCaught docsFixed since }
  readability { id title source grade note }
  glossaryCandidates { id term variants definition }
  freshness { source fresh aging stale }
  auditLog(limit: 8) { id actor verb target at }
}`;

type Res = {
  insightStats: { searches: number; answersServed: number; driftCaught: number; docsFixed: number; since: string } | null;
  readability: { id: number; title: string; source: string; grade: string; note: string }[];
  glossaryCandidates: { id: number; term: string; variants: string; definition: string }[];
  freshness: { source: string; fresh: number; aging: number; stale: number }[];
  auditLog: { id: number; actor: string; verb: string; target: string; at: string }[];
};

/* The four headline tiles. Keys, labels, tones and glyph names are the page's
   vocabulary, not the API's — insightStats supplies only the counts. */
const TILES: { key: string; field: keyof NonNullable<Res["insightStats"]>; label: string; tone: InsightStat["tone"]; icon: InsightStat["icon"] }[] = [
  { key: "searches", field: "searches", label: "searches", tone: "info", icon: "search" },
  { key: "answers", field: "answersServed", label: "answers served", tone: "ok", icon: "answers" },
  { key: "drift", field: "driftCaught", label: "drift caught", tone: "attention", icon: "drift" },
  { key: "fixed", field: "docsFixed", label: "docs fixed", tone: "ok", icon: "fixed" },
];

export function mapWidgets(res: Res): InsightsWidgetData | null {
  // insightStats is the spine of the widget block: without it there are no
  // tiles to render, and the page's own skeleton is the honest answer.
  if (!res.insightStats) return null;
  const s = res.insightStats;
  return {
    stats: TILES.map<InsightStat>((t) => ({
      key: t.key, value: Number(s[t.field] ?? 0), label: t.label, tone: t.tone, icon: t.icon,
    })),
    readability: (res.readability ?? [])
      // A document with no readability pass has an empty grade; the table's
      // whole content is the grade, so an empty row says nothing.
      .filter((r) => r.grade)
      .map<ReadRow>((r) => ({ id: r.id, title: r.title, source: r.source, grade: r.grade, note: r.note })),
    glossary: (res.glossaryCandidates ?? []).map<GlossRow>((g) => ({
      id: g.id,
      term: g.term,
      // Stored as one comma-separated string; the row renders chips.
      variants: g.variants ? g.variants.split(",").map((v) => v.trim()).filter(Boolean) : [],
      definition: g.definition,
    })),
    activity: (res.auditLog ?? []).map<InsightsActivity>((e) => ({
      id: String(e.id), actor: e.actor, action: `${e.verb} ${e.target}`.trim(), time: e.at,
      // `icon` is optional and the event log has no matching classification,
      // so the row draws its default rather than a mislabelled glyph.
    })),
    since: s.since,
  };
}

export function useInsights(): PageData<InsightsData> {
  const q = useQuery<{ widgets: InsightsWidgetData | null; freshness: Freshness[] }>(QUERY, {
    map: (d: Res) => ({ widgets: mapWidgets(d), freshness: d.freshness ?? [] }),
  });
  return {
    data: {
      widgets: q.data?.widgets ?? null,
      // `[]` is a real answer here — no sources connected yet — and `null`
      // means the query never came back, which drops the card entirely.
      freshness: q.data ? q.data.freshness : null,
      extras: null,
    },
    loading: q.loading,
    error: q.error ? (q.errorText ?? "Insights are temporarily unavailable.") : null,
  };
}
