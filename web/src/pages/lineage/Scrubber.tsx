// As-of scrubber — snaps to real event dates; the track shows activity
// density (Wayback-style). Server-driven Timeline preset logic lives in the
// page; this is presentation + the same snapping handlers, moved verbatim.

import { Ticks } from "../../components/art";
import * as Ic from "../../components/icons";
import { fmtDate } from "../../components/ui";

export function Scrubber({ eventDates, activity, maxActivity, asofIdx, lastIdx, effAsof, onStep, onSetAsof }: {
  eventDates: string[];
  activity: { date: string; count: number }[];
  maxActivity: number;
  asofIdx: number;
  lastIdx: number;
  effAsof: string | null;
  onStep: (d: number) => void;
  onSetAsof: (iso: string | null) => void;
}) {
  return (
    <div className="lineage__scrub">
      <button className="scrub__btn" onClick={() => onStep(-1)} disabled={eventDates.length === 0} aria-label="Step back one event date"><Ic.ChevL size={16} /></button>
      <button
        className="scrub__date lg-scrub-reset"
        onClick={() => onSetAsof(null)}
        title={eventDates.length ? `Events ${fmtDate(eventDates[0])} – ${fmtDate(eventDates[eventDates.length - 1])} · click to reset` : undefined}
      >
        <Ic.Calendar size={16} /> {effAsof == null ? "All time" : `As of ${fmtDate(effAsof)}`}
      </button>
      <div className="scrub__track">
        {/* activity density bars under the hand-drawn ticks */}
        <svg className="scrub__activity" viewBox="0 0 100 34" preserveAspectRatio="none" aria-hidden>
          {activity.map((a) => {
            const i = eventDates.indexOf(a.date);
            if (i < 0) return null;
            const x = eventDates.length > 1 ? (i / lastIdx) * 97 + 1.5 : 50;
            const h = 3 + (a.count / maxActivity) * 12;
            return <rect key={a.date} x={x - 0.7} y={30 - h} width={1.4} height={h} rx={0.5} fill="#b04e2c" opacity={0.5} />;
          })}
        </svg>
        <Ticks n={44} />
        <div
          className="scrub__window"
          style={{
            left: `${Math.max(0, (asofIdx / Math.max(1, lastIdx)) * 100 - 13)}%`,
            width: "14%",
          }}
        />
        <input
          className="scrub__range"
          type="range"
          min={0}
          max={lastIdx}
          value={asofIdx}
          disabled={eventDates.length === 0}
          aria-label="As-of date (snaps to event dates)"
          onChange={(e) => {
            const i = Number(e.target.value);
            onSetAsof(i >= lastIdx ? null : eventDates[i]);
          }}
        />
      </div>
      <button className="scrub__btn" onClick={() => onStep(1)} disabled={eventDates.length === 0} aria-label="Step forward one event date"><Ic.ChevR size={16} /></button>
    </div>
  );
}
