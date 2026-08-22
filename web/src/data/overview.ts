/* Overview dashboard adapter.
 *
 * This is the reference for every other `src/data/<page>.ts`: one GraphQL
 * document, one mapper from the response onto the page's exported data type,
 * one hook returning `{ data, loading, error }` straight into the page props.
 *
 * All of the Overview widgets read from root query fields that already exist,
 * so a single document fetches the whole dashboard and the page gets one
 * honest loading state instead of eight racing ones. */

import type { OverviewData } from "@mari-design/components/pages/OverviewPage";
import type { PulseTileData } from "@mari-design/components/features/OverviewSourcePulse";
import type { RecentDoc } from "@mari-design/components/features/OverviewRecentDocs";
import type { FeedItem } from "@mari-design/components/features/OverviewLiveActivity";
import type { DigestTopic } from "@mari-design/components/features/OverviewDigestCard";
import type { DateRange } from "@mari-design/components/data-display/DateRangePicker";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { projectHeaders, useQuery } from "../lib/api";
import { useAuth } from "../lib/auth";
import { rangeFromParams, rangeVars } from "./range";
import type { PageData } from "./types";

/* ── query ──────────────────────────────────────────────────────────────── */

/* `since` bounds the "changes" count, which is the one stat that counts events
   over a window; the other two are gauges of the workspace right now (facts
   awaiting review, active assistant workflows) and stay as they are whatever
   window is picked, because that is what they measure. */
const QUERY = `query Overview($since: String) {
  overviewStats(since: $since)
  digest { title summary where { source label } impact { name tone } }
  activityFeed(limit: 12) { id kind actor text target secondsAgo }
  search(query: "", k: 6) { id source title date }
  sourcePulse { provider name status stat unit bars }
}`;

/** The live feed refresh cadence the page hands to the activity widget. */
const ACTIVITY_POLL_MS = 10_000;

/* ── response shape ─────────────────────────────────────────────────────── */

type Res = {
  overviewStats: { changes: number; factsReview: number; workflowsActive: number } | null;
  digest: {
    title: string; summary: string;
    where: { source: string; label: string }[];
    impact: { name: string; tone: string }[];
  }[];
  activityFeed: { id: number; kind: string; actor: string; text: string; target: string; secondsAgo: number }[];
  search: { id: number; source: string; title: string; date: string }[];
  sourcePulse: { provider: string; name: string; status: string; stat: string; unit: string; bars: number[] }[];
};

/* ── mapping helpers ────────────────────────────────────────────────────── */

/** Source chips carry the provider's mark; the API sends only its name. */

/** Pulse tiles have two tones. Anything the API does not call active reads as
 *  the quieter one — never as an invented third state. */
const pulseStatus = (s: string): PulseTileData["status"] =>
  s.toLowerCase() === "active" ? "active" : "moderate";

/* ── mapper ─────────────────────────────────────────────────────────────── */

/** A workspace the API has not answered for yet. Not demo data: every
 *  collection is genuinely empty, and it is only ever rendered underneath
 *  `loading` or `error`. */
export const EMPTY: OverviewData = {
  personName: "",
  stats: { changes: 0, factsReview: 0, workflowsActive: 0 },
  digest: [], activity: [], docs: [], sources: [],
  activityPollMs: ACTIVITY_POLL_MS,
};

export function mapOverview(
  res: Res, personName: string, timeZone?: string, range?: DateRange,
): OverviewData {
  return {
    personName,
    // The zone Preferences collects. Omitted when the account has not been
    // asked yet, and the greeting then reads the browser's own clock — which
    // is still the reader's real local time, never a fixed hour.
    timeZone: timeZone || undefined,
    range,
    stats: {
      changes: res.overviewStats?.changes ?? 0,
      factsReview: res.overviewStats?.factsReview ?? 0,
      workflowsActive: res.overviewStats?.workflowsActive ?? 0,
    },
    digest: (res.digest ?? []).map<DigestTopic>((d) => ({
      title: d.title,
      summary: d.summary,
      where: d.where ?? [],
      impact: d.impact ?? [],
    })),
    activity: (res.activityFeed ?? []).map<FeedItem>((a) => ({
      id: a.id, kind: a.kind, actor: a.actor, text: a.text,
      target: a.target, secondsAgo: a.secondsAgo,
    })),
    docs: (res.search ?? []).map<RecentDoc>((d) => ({
      id: d.id, source: d.source, title: d.title, date: d.date,
    })),
    sources: (res.sourcePulse ?? []).map<PulseTileData>((s) => ({
      key: s.provider, name: s.name, stat: s.stat, unit: s.unit,
      status: pulseStatus(s.status), bars: s.bars ?? [],
    })),
    activityPollMs: ACTIVITY_POLL_MS,
  };
}

/* ── adapter ────────────────────────────────────────────────────────────── */

/* The signed-in account's own zone, from the same endpoint Preferences reads
   and writes — it is session state rather than workspace knowledge, so it does
   not travel with the dashboard query. "" until it answers, and the greeting
   then uses the browser's zone, which is the same clock the reader is looking
   at. No invented default: `/auth/me` does not carry a zone, so guessing one
   here would be guessing what time it is for someone. */
function useTimeZone(signedIn: boolean): string {
  const [zone, setZone] = useState("");
  useEffect(() => {
    if (!signedIn) return;
    let live = true;
    void (async () => {
      try {
        const res = await fetch("/auth/preferences", { headers: projectHeaders() });
        if (!res.ok) return;
        const body = (await res.json()) as { timezone?: string };
        if (live) setZone(String(body.timezone ?? ""));
      } catch {
        // A zone that cannot be read is not an error the dashboard reports:
        // the greeting falls back to the browser's clock and says nothing.
      }
    })();
    return () => { live = false; };
  }, [signedIn]);
  return zone;
}

export function useOverview(): PageData<OverviewData> {
  // The greeting name is session identity, not workspace content, so it comes
  // from the auth context rather than the dashboard query. Given name only:
  // "Good morning, Dana Rodriguez" reads like a summons.
  const { user } = useAuth();
  const personName = (user?.name ?? "").trim().split(/\s+/)[0] ?? "";
  const timeZone = useTimeZone(Boolean(user));

  const [params] = useSearchParams();
  // Unasked, "changes" counts the last seven days, which is what the tile has
  // always meant; the picker opens on that.
  const range = rangeFromParams(params) ?? { preset: "7d" as const };

  /* The response is cached raw and mapped on every render, not once per fetch:
     the name, the zone and the window all arrive from outside the query (auth,
     `/auth/preferences`, the route), and a mapper frozen at fetch time would
     keep showing whichever of them had not landed yet. */
  const q = useQuery<Res>(QUERY, {
    variables: { since: rangeVars(range).since },
    map: (d: Res) => d,
  });
  /* Built once per RESPONSE-AND-INPUT, not once per render. The mapping stays
     outside useQuery's map for the reason above, but `rangeFromParams` mints a
     fresh window object every render and `mapOverview` rebuilds every tile,
     feed row and digest topic off it. Keyed on the query string rather than on
     `range`, because `range` is the object with no stable identity; the other
     three inputs are primitives, so the window, the name and the zone each
     still land the moment they arrive. */
  const search = params.toString();
  const data = useMemo(() => {
    const r = rangeFromParams(new URLSearchParams(search)) ?? { preset: "7d" as const };
    return q.data ? mapOverview(q.data, personName, timeZone, r) : { ...EMPTY, range: r };
  }, [q.data, personName, timeZone, search]);
  return {
    data,
    loading: q.loading,
    error: q.error ? (q.errorText ?? "The dashboard is temporarily unavailable.") : null,
  };
}
