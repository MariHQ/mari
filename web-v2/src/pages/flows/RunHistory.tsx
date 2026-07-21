// Run status chip (design-system Chip) + the durable run-history table.

import { Chip, StatusChip, type ChipStatus } from "../../components/ui";
import { fmtDur, isDry, normStatus, runHeadline, type Run } from "./data";

const CHIP_OF: Partial<Record<string, ChipStatus>> = {
  passed: "approved",
  running: "running",
  waiting: "needs-review",
  failed: "failed",
};

/** Run lifecycle chip — maps engine statuses onto the one chip system. */
export function RunStatusChip({ status, dry }: { status: string; dry?: boolean }) {
  const n = normStatus(status);
  const label = status ? status.charAt(0).toUpperCase() + status.slice(1) : "—";
  const mapped = CHIP_OF[n];
  return (
    <span className="fl-runstatus">
      {mapped ? <StatusChip status={mapped} label={label} /> : <Chip tone="faint">{label}</Chip>}
      {dry && <span className="fl-drybadge">DRY</span>}
    </span>
  );
}

export function RunHistory({ runs, wfId, onOpen }: { runs: Run[]; wfId?: number | null; onOpen: (n: number) => void }) {
  const list = (wfId != null ? runs.filter((r) => r.workflowId === wfId) : runs).slice(0, 12);
  return (
    <div className="card fl-history">
      <h3>Run history</h3>
      <div className="card__hint">Durable and complete — every run, including tests. Click one to inspect its steps.</div>
      {list.length === 0 ? (
        <div className="card__hint" style={{ padding: "12px 0 6px" }}>No runs yet — press Run or Test run above.</div>
      ) : (
        <div className="fl-history__wrap">
          <table>
            <thead>
              <tr><th>Run</th><th>Flow</th><th>Trigger cause</th><th>Status</th><th>Duration</th><th>Headline</th></tr>
            </thead>
            <tbody>
              {list.map((r) => (
                <tr key={r.id} onClick={() => onOpen(r.number)}>
                  <td className="num">#{r.number}{isDry(r) && <> <span className="fl-drybadge">DRY</span></>}</td>
                  <td>{r.workflowName}</td>
                  <td className="dim">{r.triggeredBy || r.rows?.[0]?.detail || "—"}</td>
                  <td><RunStatusChip status={r.status} /></td>
                  <td className="dim">{fmtDur(r.duration)}</td>
                  <td className="dim headline">{runHeadline(r)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
