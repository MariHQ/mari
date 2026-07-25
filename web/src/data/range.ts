/* The window a dashboard counts over, as a route.
 *
 * Overview and Insights both grew a range picker, and both put the answer in
 * the URL rather than in component state: a narrowed dashboard is then a link
 * someone can send, and it survives a reload. The library owns the vocabulary
 * (`DateRange`, and `dateRangeStart` which resolves a preset against the
 * reader's own clock), so this file only translates between that value, the
 * query string, and the ISO bounds the server's `since` / `until` take.
 */

import { dateRangeStart, type DateRange, type DateRangePreset } from "@mari-design/components/data-display/DateRangePicker";

const PRESETS: DateRangePreset[] = ["24h", "7d", "30d", "90d", "year", "all", "custom"];

/** ISO calendar date (yyyy-mm-dd) in the reader's own zone — the bounds name
 *  days, and a UTC `toISOString()` would move the boundary for half the world. */
function isoDay(d: Date): string {
  const local = new Date(d.getTime() - d.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 10);
}

/** The window the route names, or `null` when it names none. Null is not a
 *  default window: it is "this page was not asked for one", and the page then
 *  falls back to whatever its unwindowed query already meant. */
export function rangeFromParams(params: URLSearchParams): DateRange | null {
  const preset = params.get("range");
  if (!preset || !PRESETS.includes(preset as DateRangePreset)) return null;
  if (preset === "custom") {
    const from = params.get("from") ?? "";
    const to = params.get("to") ?? "";
    // A custom range with no bounds is not a range; it is an unfinished URL.
    if (!from && !to) return null;
    return { preset: "custom", from: from || undefined, to: to || undefined };
  }
  return { preset: preset as DateRangePreset };
}

/** The `since` / `until` a windowed query takes. Both null for "all time",
 *  which is the one range with no bounds to state. */
export function rangeVars(range: DateRange | null): { since: string | null; until: string | null } {
  if (!range) return { since: null, until: null };
  if (range.preset === "custom") {
    return { since: range.from || null, until: range.to || null };
  }
  const start = dateRangeStart(range);
  return { since: start ? isoDay(start) : null, until: null };
}

/** The window as a query string, for the handler that sets it. Anything else
 *  already in the route is preserved: the range is one of several things a
 *  dashboard URL can be carrying. */
export function rangeHref(path: string, range: DateRange, current: URLSearchParams): string {
  const next = new URLSearchParams(current);
  next.set("range", range.preset);
  next.delete("from");
  next.delete("to");
  if (range.preset === "custom") {
    if (range.from) next.set("from", range.from);
    if (range.to) next.set("to", range.to);
  }
  const qs = next.toString();
  return qs ? `${path}?${qs}` : path;
}
