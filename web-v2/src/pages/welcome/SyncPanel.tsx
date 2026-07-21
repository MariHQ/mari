// Live sync progress for a just-connected source — the same syncStatus
// polling the Sources wizard uses (useSyncStatus from ../sources/syncStatus).
// Shown inside the connect drawers and on the finish step. Nothing here is
// simulated: every number comes from the server's sync registry.

import { useState } from "react";
import * as Ic from "../../components/icons";
import { Button, EmptyState, Spinner } from "../../components/ui";
import { gql } from "../../lib/api";
import { PHASES, PHASE_LABEL, useSyncStatus } from "../sources/syncStatus";

export function SyncPanel({ sourceId, label }: { sourceId: number; label: string }) {
  const { status, unreachable, refresh } = useSyncStatus(sourceId);
  const [retrying, setRetrying] = useState(false);

  const retry = async () => {
    setRetrying(true);
    await gql(`mutation($id: Int!) { syncSource(sourceId: $id) }`, { id: sourceId });
    refresh();
    setRetrying(false);
  };

  if (!status) {
    return unreachable ? (
      <EmptyState
        icon={<Ic.Globe size={22} />}
        title="Sync status unavailable"
        action={<Button compact onClick={refresh}><Ic.Refresh size={13} /> Retry</Button>}
      >
        The source was created and the sync runs on the server, but its status can’t be read right now.
      </EmptyState>
    ) : (
      <div className="wc-center"><Spinner size="sm" /> Starting sync for <b className="mono">{label}</b>…</div>
    );
  }

  const running = status.state === "running";
  const failed = status.state === "error";
  const pct = status.total > 0 ? Math.min(100, Math.round((status.done / status.total) * 100)) : 0;

  return (
    <div className="wc-sync">
      <div className="wc-phases">
        {PHASES.map((p) => {
          const idx = PHASES.indexOf((status.phase as typeof PHASES[number]) ?? "listing");
          const me = PHASES.indexOf(p);
          const state = !running ? (failed ? "" : "done") : me < idx ? "done" : me === idx ? "active" : "";
          return (
            <span key={p} className={`wc-phase${state ? ` wc-phase--${state}` : ""}`}>
              {state === "done" ? <Ic.CheckCircle size={13} /> : state === "active" ? <Spinner size="sm" /> : <Ic.Clock size={13} />}
              {PHASE_LABEL[p]}
            </span>
          );
        })}
      </div>

      {running && (
        <>
          <span className="wc-prog">
            <span className="wc-prog__track"><span className="wc-prog__fill" style={{ width: `${pct}%` }} /></span>
            <span className="wc-prog__n">{status.total > 0 ? `${status.done}/${status.total} files` : "listing files…"}</span>
          </span>
          <div className="card__hint">
            {PHASE_LABEL[status.phase] ?? status.phase} · {status.chunkCount.toLocaleString()} chunks ·{" "}
            {status.embeddedCount.toLocaleString()} embedded
          </div>
        </>
      )}

      {failed && (
        <div className="wc-error" role="alert">
          <b>Sync failed.</b> {status.lastError || "The server reported an error without details."}
          <div className="wc-row">
            <Button compact disabled={retrying} onClick={retry}>
              {retrying ? <><Spinner size="sm" /> Retrying…</> : <><Ic.Refresh size={13} /> Retry sync</>}
            </Button>
          </div>
        </div>
      )}

      {!running && !failed && (
        <div className="wc-done">
          <span className="wc-ok"><Ic.CheckCircle size={15} /> <b className="mono">{label}</b> is synced.</span>
          <div className="wc-done__stats">
            {[[status.docCount, "documents"], [status.chunkCount, "chunks"], [status.embeddedCount, "embedded"]].map(([n, l]) => (
              <span key={l as string} className="wc-done__stat"><b>{Number(n).toLocaleString()}</b> {l}</span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/** One-line live status for the finish summary — same polling, compact. */
export function SyncSummaryLine({ sourceId }: { sourceId: number }) {
  const { status, unreachable } = useSyncStatus(sourceId);
  if (!status) {
    return <span className="card__hint">{unreachable ? "sync status unreachable" : "checking sync status…"}</span>;
  }
  if (status.state === "running") {
    return (
      <span className="wc-sum-live">
        <Spinner size="sm" /> sync running — {PHASE_LABEL[status.phase] ?? status.phase}
        {status.total > 0 ? ` · ${status.done}/${status.total} files` : ""}
      </span>
    );
  }
  if (status.state === "error") {
    return <span className="wc-sum-err">sync failed: {status.lastError || "no details from the server"}</span>;
  }
  return (
    <span className="card__hint">
      synced · {status.docCount.toLocaleString()} docs · {status.chunkCount.toLocaleString()} chunks ·{" "}
      {status.embeddedCount.toLocaleString()} embedded
    </span>
  );
}
