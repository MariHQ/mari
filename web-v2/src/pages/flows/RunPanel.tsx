// Run panel — the design-system Drawer (which generalized this page's old
// .fl-drawer), deep-linkable via ?run=<number>. backdrop={false}: the page
// stays live behind it so the list dots keep updating while a run polls.

import * as Ic from "../../components/icons";
import { Button, Drawer, Spinner } from "../../components/ui";
import { fmtDur, fmtStarted, isDry, normStatus, type Run } from "./data";
import { RunStatusChip } from "./RunHistory";

function TimelineIcon({ status }: { status: string }) {
  const n = normStatus(status);
  if (n === "passed") return <Ic.Check size={12} strokeWidth={2.4} />;
  if (n === "running") return <Spinner size="sm" label="Step running" />;
  if (n === "waiting") return <>⧗</>;
  if (n === "failed") return <Ic.Close size={12} strokeWidth={2.2} />;
  if (n === "skipped") return <>↷</>;
  return <span className="fl-tl__pend" />;
}

export function RunPanel({ run, number, onClose, onApprove, onRerun, busy, note }: {
  run: Run | null; number: string; onClose: () => void;
  onApprove: (run: Run) => void; onRerun: (run: Run, dry: boolean) => void;
  busy: boolean; note: string | null;
}) {
  const stats = run?.stats ?? {};
  const statEntries: [string, number, boolean][] = [];
  for (const [key, label] of [["edits", "Edits"], ["contradictions", "Contradictions"], ["links", "Links"], ["facts", "Facts"]] as [string, string][]) {
    if (typeof stats[key] === "number") statEntries.push([label, stats[key], key === "contradictions" && stats[key] > 0]);
  }
  const waitingRow = run?.rows?.find((r) => normStatus(r.status) === "waiting");

  return (
    <Drawer
      open
      onOpenChange={(o) => { if (!o) onClose(); }}
      backdrop={false}
      title={`Run #${number}`}
      meta={run && (
        <span className="fl-runstatus">
          <RunStatusChip status={run.status} />
          {isDry(run) && <span className="fl-drystamp">DRY RUN</span>}
        </span>
      )}
      footer={
        <>
          {run?.status === "waiting" && (
            <Button variant="primary" disabled={busy} onClick={() => onApprove(run)}>
              <Ic.Check size={13} /> Approve & resume
            </Button>
          )}
          {run && (
            <>
              <Button disabled={busy} title="Re-runs every step for real, including side effects" onClick={() => onRerun(run, false)}>
                <Ic.Refresh size={13} /> Re-run
              </Button>
              <Button disabled={busy} title="Re-runs transforms for real; side effects become previews" onClick={() => onRerun(run, true)}>
                <Ic.Eye size={13} /> Re-run as test
              </Button>
            </>
          )}
          <span className="card__spacer" />
          <Button onClick={onClose}>Close</Button>
        </>
      }
    >
      {!run ? (
        <div className="fl-starting">
          <Spinner size="sm" label="Starting" /> Starting — the engine is picking this run up…
        </div>
      ) : (
        <>
          <div className="fl-runmeta">
            {run.workflowName} · started {fmtStarted(run.started)} · {fmtDur(run.duration)}
            {isDry(run) && " · no side effects were written"}
          </div>
          {run.triggeredBy && (
            <div className="fl-runmeta fl-runmeta--provenance">
              <Ic.Bell size={12} /> {run.triggeredBy}
            </div>
          )}

          {run.rows?.length ? (
            <ol className="fl-tl">
              {run.rows.map((r, i) => (
                <li key={`${r.step}-${i}`} className={normStatus(r.status)}>
                  <span className="fl-tl__icon"><TimelineIcon status={r.status} /></span>
                  <span className="fl-tl__main">
                    <span className="fl-tl__name">
                      {r.step}
                      {r.duration != null && <span className="fl-tl__dur">{fmtDur(r.duration)}</span>}
                    </span>
                    <div className="fl-tl__detail">{r.detail || "—"}</div>
                  </span>
                </li>
              ))}
            </ol>
          ) : (
            <div className="card__hint" style={{ marginTop: 12 }}>
              The step log wasn't retained for this run — the stats below are the recorded outcome.
            </div>
          )}

          {statEntries.length > 0 && (
            <div className="fl-stats" style={{ gridTemplateColumns: `repeat(${Math.min(4, statEntries.length)}, 1fr)` }}>
              {statEntries.map(([k, v, bad]) => (
                <div key={k}>
                  <div className="fl-stats__k">{k}</div>
                  <div className={`fl-stats__v${bad ? " bad" : ""}`}>{v}</div>
                </div>
              ))}
            </div>
          )}

          {run.status === "waiting" && (
            <div className="fl-approvebox">
              Paused for approval{waitingRow?.detail ? ` — ${waitingRow.detail}` : ""}. Nothing downstream runs until someone approves.
            </div>
          )}
        </>
      )}
      {note && <div className="card__hint" style={{ marginTop: 10, color: "var(--green-deep)" }}>{note}</div>}
    </Drawer>
  );
}
