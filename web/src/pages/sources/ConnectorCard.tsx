// One connected-source pulse card. GitHub and connector-framework sources show
// live state from the `syncStatus` contract query (polled only while a sync
// runs) with real Sync now / Full resync actions. Legacy seeded sources render
// exactly what the server reports — no client-side fabricated counts or dates.

import { useState } from "react";
import { Bars } from "../../components/art";
import * as Ic from "../../components/icons";
import { Button, Menu, MenuItem, Spinner, fmtDateTime } from "../../components/ui";
import { gql } from "../../lib/api";
import { ProviderMark } from "./connectors";
import { PHASE_LABEL, useSyncStatus } from "./syncStatus";

export function ConnectorCard({
  source: s,
  onChanged,
}: {
  source: any;
  /** Fired after a mutation so the parent can refetch source rows. */
  onChanged: () => void;
}) {
  // Real GitHub sources: provider "github:owner/repo" (kind 'github').
  // Connector-framework sources: kind 'connector' — recognisable by the
  // provider_key the server keeps in (masked) config. Bare "github"/"slack"/…
  // providers are legacy seeds.
  const baseProvider: string = String(s.provider ?? "").split(":")[0];
  const isGithubKind = String(s.provider ?? "").startsWith("github:");
  const isConnectorKind = Boolean(s.config?.provider_key);
  // A live source without an id (server doesn't expose sourcePulse.id yet):
  // render its server-reported data, but sync actions need the id.
  const isLive = (isGithubKind || isConnectorKind) && typeof s.id === "number";
  const actionless = (isGithubKind || isConnectorKind) && !isLive;
  const { status, refresh } = useSyncStatus(isLive ? s.id : null);
  const [busy, setBusy] = useState(false);

  const running = status?.state === "running";
  const failed = status?.state === "error";

  const run = async (mutation: string) => {
    setBusy(true);
    await gql(mutation, isLive ? { id: s.id } : { p: s.provider });
    if (isLive) refresh();
    onChanged();
    setBusy(false);
  };

  const syncNow = () =>
    run(isLive
      ? `mutation($id: Int!) { syncSource(sourceId: $id) }`
      : `mutation($p: String!) { syncNow(provider: $p) }`);
  const fullResync = () => run(`mutation($id: Int!) { resyncSource(sourceId: $id) }`);
  const pause = () => run(`mutation($p: String!) { disconnectSource(provider: $p) }`);
  const resume = () =>
    run(`mutation($p: String!) { syncNow(provider: $p) }`); // legacy resume = server-side sync kick

  const isPaused = s.status === "paused";
  const health = isLive
    ? (running ? "Syncing" : failed ? "Error" : "Healthy")
    : isPaused ? "Paused" : (s.health || "Healthy");
  const healthClass = failed
    ? "src-card__health--error"
    : isPaused ? "src-card__health--paused"
    : running || health === "Backfilling" ? "src-card__health--backfilling" : "";

  const countsLine = isLive && status
    ? `${status.docCount.toLocaleString()} documents · ${status.chunkCount.toLocaleString()} chunks · ${status.embeddedCount.toLocaleString()} embedded`
    : s.docsCount != null
      ? `${Number(s.docsCount).toLocaleString()} documents`
      : s.stat ? `${s.stat} ${s.unit ?? ""}`.trim() : "";

  const syncLine = () => {
    if (isPaused) return <button className="linklike" onClick={resume} disabled={busy}>Paused — resume syncing</button>;
    if (busy) return <><Spinner size="sm" /> Working…</>;
    if (actionless) {
      const at = s.config?.last_sync_at;
      return at ? `Last sync: ${fmtDateTime(at)}` : <span className="card__hint">Sync status unavailable</span>;
    }
    if (!isLive) return null; // legacy sources: no client-side "last sync" fiction
    if (!status) return <span className="card__hint">Sync status unavailable</span>;
    if (running) {
      return (
        <>
          <Spinner size="sm" /> {PHASE_LABEL[status.phase] ?? status.phase}
          {status.total > 0 ? ` · ${status.done}/${status.total} items` : "…"}
        </>
      );
    }
    if (failed) return <span className="src-card__err" title={status.lastError}>Sync failed — {status.lastError || "no details"}</span>;
    return status.lastSyncAt ? `Last sync: ${fmtDateTime(status.lastSyncAt)}` : "Never synced";
  };

  const bars: number[] | null = Array.isArray(s.bars) && s.bars.length ? s.bars : null;
  const markProvider = isConnectorKind ? String(s.config?.provider_key ?? baseProvider) : baseProvider;

  return (
    <div className={`card src-card${isPaused ? " src-card--paused" : ""}`}>
      <div className="src-card__head">
        <ProviderMark provider={markProvider} size={26} />
        <span className="src-card__title">
          <b className="src-card__name">{s.name}</b>
          <span className={`src-card__health ${healthClass}`}>
            {isPaused ? <Ic.Clock size={12} />
              : failed ? <Ic.Close size={12} />
              : running || health === "Backfilling" ? <Ic.Play size={12} />
              : <Ic.CheckCircle size={12} />} {health}
          </span>
        </span>
        <Menu trigger={<Button icon aria-label={`Actions for ${s.name}`}><Ic.Kebab size={15} /></Button>}>
          <MenuItem icon={<Ic.Refresh size={13} />} disabled={busy || running || isPaused || actionless} onSelect={syncNow}>
            {running ? "Sync running…" : "Sync now"}
          </MenuItem>
          {(isGithubKind || isConnectorKind) && (
            <MenuItem icon={<Ic.Sparkle size={13} />} disabled={busy || running || actionless} onSelect={fullResync}>
              Full resync
            </MenuItem>
          )}
          {!isGithubKind && !isConnectorKind && (isPaused
            ? <MenuItem icon={<Ic.Play size={13} />} disabled={busy} onSelect={resume}>Resume</MenuItem>
            : <MenuItem icon={<Ic.Clock size={13} />} disabled={busy} onSelect={pause}>Pause</MenuItem>)}
        </Menu>
      </div>
      {countsLine && <div className="src-card__counts">{countsLine}</div>}
      <div className="src-card__sync">{syncLine()}</div>
      {bars && (
        <div className="src-card__bars">
          <Bars values={[...bars, ...bars]} width={150} height={20} color={s.status === "moderate" ? "var(--gold)" : "var(--green)"} />
        </div>
      )}
    </div>
  );
}
