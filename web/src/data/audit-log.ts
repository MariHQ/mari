/* Settings → Access log adapter. A straight pass-through of the event log:
 * the feature owns its own filtering, sorting and paging over the window it
 * is given, so the adapter's only job is to fetch that window — and to say
 * how deep the log it is a window onto actually goes. */

import type { PropertyItem } from "@mari-design/components";
import type { AuditDetail, SettingsAuditLogData } from "@mari-design/components/pages/SettingsAuditLogPage";
import type { AuditEvent } from "@mari-design/components/features/SettingsAuditLog";
import { useQuery } from "../lib/api";
import type { PageData } from "./types";

/* The log is thousands of rows deep and the table pages 15 at a time; 200 is
   a window deep enough to filter within without shipping the whole history. */
const WINDOW = 200;

const QUERY = `{
  auditLog(limit: ${WINDOW}) { id actor verb target at detail { label value } }
  auditLogTotal
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

/** Pure: the fetched window, the log's real depth, and which row is expanded
 *  → everything the page renders. */
export function buildAuditLog(
  events: AuditEvent[],
  total: number,
  details: Map<number, AuditDetail[]>,
  expandedId: number | null,
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
    filter: null,
    expandedId,
    // The expanded row's detail, resolved out of the same rows the table
    // draws. Nothing expanded, nothing to show.
    detail: expandedId === null ? [] : details.get(expandedId) ?? [],
    // The feature pages internally, so the page-level pager stays off.
    pager: null,
    summary,
  };
}

export function useSettingsAuditLog(): PageData<SettingsAuditLogData> {
  const q = useQuery<Res>(QUERY, { map: (d: Res) => d });
  // Which row is open is a click this app has no route for yet: the pages are
  // pure presenters and take no callbacks. The detail is fetched and resolved
  // alongside the rows regardless, so a click handler would have nothing left
  // to fetch.
  const expandedId: number | null = null;
  return {
    data: buildAuditLog(
      q.data ? mapAuditLog(q.data) : [],
      q.data?.auditLogTotal ?? 0,
      q.data ? mapDetails(q.data) : new Map(),
      expandedId,
    ),
    loading: q.loading,
    error: q.error ? (q.errorText ?? "The access log is temporarily unavailable.") : null,
  };
}
