/* Canonical date formatting for Mari Cloud.
 *
 * Rules (from the consistency audit):
 * - fmtDate:      "Jul 16"           — same-year dates omit the year.
 *                 "Jul 16, 2025"     — other years include it.
 * - fmtTime:      "2:17 PM"
 * - fmtDateTime:  "Jul 16, 2:17 PM"  — timestamps belong in feeds/activity
 *                 rows only; everywhere else use fmtDate.
 */

export type DateInput = Date | string | number;

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/;

function toDate(input: DateInput): Date {
  if (input instanceof Date) return input;
  if (typeof input === "string") {
    // Parse date-only strings as LOCAL dates ("2026-07-14" must not shift a day).
    const m = DATE_ONLY.exec(input);
    if (m) return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  }
  return new Date(input);
}

/** "Jul 16" (same year) or "Jul 16, 2025" (other years). */
export function fmtDate(input: DateInput, now: Date = new Date()): string {
  const d = toDate(input);
  if (Number.isNaN(d.getTime())) return String(input);
  const base = `${MONTHS[d.getMonth()]} ${d.getDate()}`;
  return d.getFullYear() === now.getFullYear() ? base : `${base}, ${d.getFullYear()}`;
}

/** "2:17 PM" */
function fmtTime(input: DateInput): string {
  const d = toDate(input);
  if (Number.isNaN(d.getTime())) return String(input);
  const h24 = d.getHours();
  const h = h24 % 12 === 0 ? 12 : h24 % 12;
  const m = String(d.getMinutes()).padStart(2, "0");
  return `${h}:${m} ${h24 < 12 ? "AM" : "PM"}`;
}

/** "Jul 16, 2:17 PM" — feeds and activity rows only. */
export function fmtDateTime(input: DateInput, now: Date = new Date()): string {
  const d = toDate(input);
  if (Number.isNaN(d.getTime())) return String(input);
  return `${fmtDate(d, now)}, ${fmtTime(d)}`;
}

/** Relative time for status chips: "just now", "5m ago", "3h ago", "2d ago";
 *  older than a week falls back to fmtDate. Returns "" for unparsable input
 *  so callers can render nothing instead of a lie. */
export function fmtAgo(input: DateInput, now: Date = new Date()): string {
  const d = toDate(input);
  if (Number.isNaN(d.getTime())) return "";
  const s = Math.max(0, Math.floor((now.getTime() - d.getTime()) / 1000));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  if (s < 7 * 86400) return `${Math.floor(s / 86400)}d ago`;
  return fmtDate(d, now);
}
