/* Settings → Access log adapter. A straight pass-through of the event log:
 * the feature owns its own filtering, sorting and paging over the window it
 * is given, so the adapter's only job is to fetch that window — and to say
 * how deep the log it is a window onto actually goes. */

import { useSearchParams } from "react-router-dom";
import type { PropertyItem } from "@mari-design/components";
import type { AuditDetail, SettingsAuditLogData } from "@mari-design/components/pages/SettingsAuditLogPage";
import type { AuditEvent } from "@mari-design/components/features/SettingsAuditLog";
import { useQuery } from "../lib/api";
import type { PageData } from "./types";

/* The log is thousands of rows deep and the table pages 15 at a time; 200 is
   a window deep enough to filter within without shipping the whole history. */
const WINDOW = 200;

/* The filter reaches the whole table, not just the window already fetched:
   the log is thousands of rows deep, so narrowing it client-side would search
   the most recent 200 events and call that the answer. It lives in the route
   (`?q=`, `?from=`, `?to=`) so a filtered log is a link. */
const QUERY = `query AuditLog($q: String!, $from: String, $to: String) {
  auditLog(limit: ${WINDOW}, query: $q, dateFrom: $from, dateTo: $to) {
    id actor verb target at detail { label value }
  }
  auditLogTotal(query: $q, dateFrom: $from, dateTo: $to)
}`;

type Res = {
  auditLog: {
    id: number; actor: string; verb: string; target: string; at: string;
    detail: { label: string; value: string }[];
  }[];
  auditLogTotal: number;
};

export function mapAuditLog(res: Res): AuditEvent[] {
  return (res.auditLog ?? []).map<AuditEvent>((e) => ({
    id: e.id, actor: e.actor, verb: e.verb, target: e.target, at: e.at,
    // Detail rides every row now, because every row can be expanded. [] is
    // recorded truth for events logged before the writer had more to say, and
    // the table draws no expand control for them.
    detail: (e.detail ?? []).map<AuditDetail>((d) => ({ label: d.label, value: d.value })),
  }));
}

/** What the expanded row shows beyond actor/verb/target/at, per event id.
 *  Recorded at write time by whoever logged the event, so it is [] for events
 *  logged before the writer had anything more to say. */
export function mapDetails(res: Res): Map<number, AuditDetail[]> {
  return new Map((res.auditLog ?? []).map((e) => [
    e.id,
    (e.detail ?? []).map<AuditDetail>((d) => ({ label: d.label, value: d.value })),
  ]));
}

/** The applied filter, as the page labels it. `null` for the unfiltered log —
 *  which is not the same as a filter that matched nothing. */
export function filterLabel(query: string, from: string, to: string): SettingsAuditLogData["filter"] {
  const parts = [
    query ? `“${query}”` : "",
    from && to ? `${from} to ${to}` : from ? `from ${from}` : to ? `until ${to}` : "",
  ].filter(Boolean);
  if (!parts.length) return null;
  return { label: parts.join(" · "), query: query || undefined };
}

/** Pure: the fetched window, the log's real depth, and which row is expanded
 *  → everything the page renders. */
export function buildAuditLog(
  events: AuditEvent[],
  total: number,
  details: Map<number, AuditDetail[]>,
  expandedId: number | null,
  filter: SettingsAuditLogData["filter"] = null,
): SettingsAuditLogData {
  const actors = new Set(events.map((e) => e.actor));
  const summary: PropertyItem[] = events.length
    ? [
        { label: "Events in window", value: String(events.length) },
        { label: "Events in log", value: total.toLocaleString("en-US") },
        { label: "Distinct actors", value: String(actors.size) },
        // Rows come back newest first, so the last one is the window's floor.
        { label: "Oldest shown", value: events[events.length - 1].at, stacked: true },
      ]
    : [];

  return {
    events,
    // `auditLogTotal` counts the whole `events` table, so the count strip can
    // say what its window is a window onto instead of comparing a filtered
    // view against the window's own size.
    total,
    filter,
    expandedId,
    // The expanded row's detail, resolved out of the same rows the table
    // draws. Nothing expanded, nothing to show.
    detail: expandedId === null ? [] : details.get(expandedId) ?? [],
    // No `pager`: the feature pages itself over the window it is given, and
    // the page-level pager is legacy — nothing reads it.
    summary,
  };
}

export function useSettingsAuditLog(): PageData<SettingsAuditLogData> {
  const [params] = useSearchParams();
  const query = params.get("q") ?? "";
  const from = params.get("from") ?? "";
  const to = params.get("to") ?? "";
  // Which row starts expanded is a deep link; every row can be opened by hand,
  // so an absent `?event=` simply means none of them starts open.
  const asked = Number(params.get("event"));
  const expandedId = Number.isInteger(asked) && asked > 0 ? asked : null;

  const q = useQuery<Res>(QUERY, {
    variables: { q: query, from: from || null, to: to || null },
    map: (d: Res) => d,
  });
  return {
    data: buildAuditLog(
      q.data ? mapAuditLog(q.data) : [],
      q.data?.auditLogTotal ?? 0,
      q.data ? mapDetails(q.data) : new Map(),
      expandedId,
      filterLabel(query, from, to),
    ),
    loading: q.loading,
    error: q.error ? (q.errorText ?? "The access log is temporarily unavailable.") : null,
  };
}
