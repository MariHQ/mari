// Assertion / impact-analysis drawer — same .lg-drawer shell. All analysis
// state lives in the page so results survive closing and reopening the drawer.

import clsx from "clsx";
import * as Ic from "../../components/icons";
import { Button } from "../../components/ui";
import { ImpactResult } from "./model";

const BUCKETS = [
  { key: "red" as const, label: "Direct contradiction", tone: "#c0392b", severity: "update-required" },
  { key: "gold" as const, label: "Needs update", tone: "#d0862c", severity: "review" },
  { key: "blue" as const, label: "Mentions", tone: "#35549d", severity: "minor" },
];

const SEV_CLASS: Record<string, string> = {
  "update-required": "lg-sev--update", review: "lg-sev--review",
};
const SEV_LABEL: Record<string, string> = {
  "update-required": "Update required", review: "Review",
};

export function AssertDrawer({ claim, onClaim, analyzing, onAnalyze, result, analyzedAt, taskState, onCreateTasks, onExport, severityFilter, onSeverityFilter, onClose }: {
  claim: string;
  onClaim: (v: string) => void;
  analyzing: boolean;
  onAnalyze: () => void;
  result: ImpactResult | null;
  analyzedAt: string | null;
  taskState: "idle" | "creating" | "done" | "error";
  onCreateTasks: () => void;
  onExport: () => void;
  severityFilter: string | null;
  onSeverityFilter: (severity: string) => void;
  onClose: () => void;
}) {
  const bucketCounts = result
    ? {
        red: result.docs.filter((x) => x.severity === "update-required").length,
        gold: result.docs.filter((x) => x.severity === "review").length,
        blue: result.docs.filter((x) => x.severity === "minor").length,
      }
    : { red: 3, gold: 11, blue: 22 };
  const needUpdates = result ? bucketCounts.red + bucketCounts.gold : 14;
  const taskCount = result ? result.docs.length : 14;

  return (
    <aside className="lg-drawer" role="dialog" aria-label="Assert impact">
      <div className="lg-drawer__head">
        <span className="lg-drawer__icon"><Ic.Sparkle size={17} /></span>
        <div className="lg-grow">
          <b className="lg-drawer__title">Impact analysis</b>
          <div className="lg-hint">What does changing this touch?</div>
        </div>
        <button className="kebab lg-drawer__close" onClick={onClose} aria-label="Close impact analysis">✕</button>
      </div>
      <div className="lg-drawer__body">
        <div>
          <span className="card__hint">Assertion</span>
          <div className="row lg-claim">
            <span className="lg-claim__quote">“ </span>
            <input
              name="lineage-claim"
              className="lg-claim__input"
              value={claim}
              onChange={(e) => onClaim(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && onAnalyze()}
            />
            <span className="lg-claim__quote"> ”</span>
          </div>
          <Button className={clsx("lg-analyze", analyzing && "lg-busy")} onClick={onAnalyze} disabled={analyzing}>
            <Ic.Sparkle size={13} /> {analyzing ? "Analyzing…" : "Analyze"}
          </Button>
        </div>
        <div className="btn lg-assert-status">
          <Ic.CheckCircle size={15} style={{ color: analyzing ? "var(--gold, #c8973a)" : analyzedAt ? "var(--green-deep)" : "var(--ink-faint)" } as any} />
          <span>
            <b className="lg-semibold">{analyzing ? "Running" : analyzedAt ? "Completed" : "Ready"}</b><br />
            <span className="card__hint">{analyzing ? "Mari is reading the graph…" : analyzedAt ?? "Enter an assertion and analyze"}</span>
          </span>
        </div>

        {result?.summary && <div className="lg-assert-summary">{result.summary}</div>}

        <div className="lg-buckets">
          <div className="lg-buckets__head">
            <b className="lg-buckets__n">{needUpdates}</b> documents need updates
          </div>
          {BUCKETS.map((r) => {
            const active = severityFilter === r.severity;
            return (
              <button
                key={r.label}
                className={clsx("row lg-bucket", active && "is-active")}
                disabled={!result}
                title={result ? (active ? "Show all impacted documents" : `Show only: ${r.label}`) : "Run the analysis first"}
                aria-pressed={active}
                onClick={() => onSeverityFilter(r.severity)}
              >
                <span className="lg-bucket__dot" style={{ background: r.tone }}>!</span>
                <b className="lg-semibold">{bucketCounts[r.key]}</b> {r.label}
                <span className="lg-bucket__chev">
                  <Ic.ChevR size={13} style={active ? { transform: "rotate(90deg)" } : undefined} />
                </span>
              </button>
            );
          })}
        </div>

        {result && result.docs.length > 0 && (
          <div className="lg-impactlist">
            {severityFilter != null && result.docs.every((doc) => doc.severity !== severityFilter) && (
              <div className="card__hint lg-impactlist__empty">No documents in this bucket — click it again to show all.</div>
            )}
            {result.docs.filter((doc) => severityFilter == null || doc.severity === severityFilter).map((doc, i) => (
              <div key={i} className="lg-impactrow">
                <div className="row" style={{ justifyContent: "space-between", gap: 8 }}>
                  <b className="lg-semibold">{doc.title}</b>
                  <span className={clsx("lg-sev", SEV_CLASS[doc.severity] ?? "lg-sev--minor")}>
                    {SEV_LABEL[doc.severity] ?? "Minor"}
                  </span>
                </div>
                <div className="lg-impactrow__sub">{doc.source} — {doc.reason}</div>
              </div>
            ))}
          </div>
        )}

        <div>
          <span className="card__hint">Top owners</span>
          <div className="row lg-owners">
            {[["AK", 2], ["LS", 1], ["MM", 1], ["JS", 4]].map(([i, t]) => (
              <span key={i as string} className="lg-owners__cell">
                <span className={`avatar avatar--tint${t} lg-av32`}>{i}</span>
              </span>
            ))}
            <span className="avatar lg-av32 lg-av32--plus">+3</span>
          </div>
        </div>
      </div>
      <div className="lg-drawer__foot">
        <Button
          variant="primary"
          block
          className={clsx("lg-cta", (!result || taskState === "creating") && "lg-busy")}
          onClick={onCreateTasks}
          disabled={!result || taskState === "creating"}
          title={result ? undefined : "Run the analysis first"}
        >
          {taskState === "done" ? (
            <>Created {taskCount} tasks <Ic.Check size={15} strokeWidth={2.2} /></>
          ) : taskState === "creating" ? (
            <>Creating tasks…</>
          ) : taskState === "error" ? (
            <>Retry creating tasks <Ic.ArrowR size={15} /></>
          ) : (
            <>Create {taskCount} tasks <Ic.ArrowR size={15} /></>
          )}
        </Button>
        {taskState === "error" && <span className="lg-hint">Couldn’t reach Mari — some tasks were not created.</span>}
        <Button block onClick={onExport}><Ic.Send size={14} /> Export report</Button>
        <div className="row lg-assert-scope">
          <span className="row" style={{ gap: 7 }}><Ic.Globe size={14} /> Analysis scope</span>
          <span>All reachable documents <Ic.ChevR size={11} /></span>
        </div>
      </div>
    </aside>
  );
}
