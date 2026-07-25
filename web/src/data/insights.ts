/* Insights adapter. Four root fields feed the widget block; freshness feeds
 * its own card and is nullable on the page, so the two are queried together
 * but mapped apart. */

import { useSearchParams } from "react-router-dom";
import type { InsightsData, InsightsWidgetData } from "@mari-design/components/pages/InsightsPage";
import type { InsightStat, ReadRow, GlossRow, InsightsActivity } from "@mari-design/components/features/InsightsWidgets";
import type { Freshness } from "@mari-design/components/features/InsightsFreshnessChart";
import { useQuery } from "../lib/api";
import { rangeFromParams, rangeVars } from "./range";
import type { PageData } from "./types";

/* The window is a route (`?range=30d`, or `?range=custom&from=&to=`), so a
   dashboard someone narrowed is a link they can send. `since`/`until` bound
   every count `insightStats` returns, which is what lets the page's own
   sentence — "over the last 30 days, from …" — describe all four tiles. */
const QUERY = `query Insights($since: String, $until: String) {
  insightStats(since: $since, until: $until) { searches answersServed driftCaught docsFixed since }
  readability { id title source grade note }
  glossaryCandidates { id term variants definition }
  freshness { source provider fresh aging stale }
  auditLog(limit: 8) { id actor verb target at }
}`;

type Res = {
  insightStats: { searches: number; answersServed: number; driftCaught: number; docsFixed: number; since: string } | null;
  readability: { id: number; title: string; source: string; grade: string; note: string }[];
  glossaryCandidates: { id: number; term: string; variants: string; definition: string }[];
  freshness: { source: string; provider: string; fresh: number; aging: number; stale: number }[];
  auditLog: { id: number; actor: string; verb: string; target: string; at: string }[];
};

/** The chart draws the provider's mark off `source` and prints `label`. The
 *  API answers with both, so a row names the source the workspace named — the
 *  chart used to hold a table that turned "github" into
 *  "GitHub · product-docs", a repository it had no way of knowing. */
export function mapFreshness(res: Res): Freshness[] {
  return (res.freshness ?? []).map<Freshness>((f) => ({
    source: f.provider || f.source,
    // Only when it says something the provider key does not.
    label: f.source && f.source !== f.provider ? f.source : undefined,
    fresh: f.fresh, aging: f.aging, stale: f.stale,
  }));
}

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
  const [params] = useSearchParams();
  // Unasked, the dashboard counts everything, which is what it did before it
  // had a picker. The picker then opens on that window rather than on a
  // default it is not actually showing.
  const range = rangeFromParams(params) ?? { preset: "all" as const };
  const q = useQuery<{ widgets: InsightsWidgetData | null; freshness: Freshness[] }>(QUERY, {
    variables: rangeVars(range),
    map: (d: Res) => ({ widgets: mapWidgets(d), freshness: mapFreshness(d) }),
  });
  return {
    data: {
      widgets: q.data?.widgets ?? null,
      // `[]` is a real answer here — no sources connected yet — and `null`
      // means the query never came back, which drops the card entirely.
      freshness: q.data ? q.data.freshness : null,
      range,
      extras: null,
    },
    loading: q.loading,
    error: q.error ? (q.errorText ?? "Insights are temporarily unavailable.") : null,
  };
}
