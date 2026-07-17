// Settings → Sources & connectors.
// GitHub is the real connector (GITHUB-SYNC-CONTRACT.md): per-repo sources,
// live sync state, diff-based syncs. Everything shown here comes from the
// server — loading and error states are rendered honestly, never canned rows.

import { Fragment } from "react";
import { useNavigate } from "react-router-dom";
import * as Ic from "../../components/icons";
import { GitHubMark } from "../../components/icons";
import { Button, Chip, EmptyState, PageHeader, Spinner, Tabs, fmtDateTime } from "../../components/ui";
import { gql, useQuery } from "../../lib/api";
import { useCallback, useEffect, useState } from "react";
import { BotsTab } from "./Bots";
import { ConnectorCard } from "./ConnectorCard";
import { ConnectorWizard } from "./ConnectorWizard";
import { CatalogEntry, PROVIDER_NAME, ProviderMark, fetchCatalog } from "./connectors";
import "../sources.css";

const PROVIDER_SUB: Record<string, string> = {
  github: "Repository", slack: "Channel", gdocs: "Folder", notion: "Database", granola: "Agent", upload: "Upload",
};

// Ingestion stages, matching the contract's sync phases.
const STAGES = ["Listing", "Fetching", "Chunking", "Embedding", "Indexing"];
const STAGE_ICONS = [<Ic.Search size={15} />, <Ic.Globe size={15} />, <Ic.Layers size={15} />, <Ic.Sparkle size={15} />, <Ic.CheckCircle size={15} />];

type SourceRow = {
  id?: number;
  provider: string;
  name: string;
  status: string;
  stat: string;
  unit: string;
  bars: number[];
  docsCount: number | null;
  health: string;
  config: Record<string, any> | null;
};

// Sync actions need each source's id (syncStatus/syncSource take sourceId).
// `sourcePulse.id` isn't in the frozen contract, so if the server doesn't
// expose it yet we fall back to the id-less query: still 100% live data, but
// GitHub cards degrade to "sync status unavailable" until ids arrive.
const SOURCES_QUERY = `{ sourcePulse { id provider name status stat unit bars docsCount health config } }`;
const SOURCES_QUERY_NO_ID = `{ sourcePulse { provider name status stat unit bars docsCount health config } }`;

function useSources() {
  const [state, setState] = useState<{ data: SourceRow[] | null; loading: boolean; error: boolean }>({
    data: null, loading: true, error: false,
  });
  const [nonce, setNonce] = useState(0);
  const refetch = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    let alive = true;
    (async () => {
      let d = await gql<{ sourcePulse: SourceRow[] }>(SOURCES_QUERY);
      if (!d) d = await gql<{ sourcePulse: SourceRow[] }>(SOURCES_QUERY_NO_ID);
      if (!alive) return;
      setState(d?.sourcePulse
        ? { data: d.sourcePulse, loading: false, error: false }
        : { data: null, loading: false, error: true });
    })();
    return () => { alive = false; };
  }, [nonce]);

  return { ...state, refetch };
}

/** Connector catalog — server truth from GET /connectors/catalog. */
function useCatalog() {
  const [state, setState] = useState<{ data: CatalogEntry[] | null; loading: boolean; error: boolean }>({
    data: null, loading: true, error: false,
  });
  const [nonce, setNonce] = useState(0);
  const refetch = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    let alive = true;
    (async () => {
      const cat = await fetchCatalog();
      if (!alive) return;
      setState(cat ? { data: cat, loading: false, error: false } : { data: null, loading: false, error: true });
    })();
    return () => { alive = false; };
  }, [nonce]);

  return { ...state, refetch };
}

const CHECKPOINTS_QUERY = `{ checkpoints { id provider item stage progress total cursorId duration status } }`;
const EVENTS_QUERY = `{ syncEvents { id provider event detail at } }`;

const baseProvider = (provider: string) => provider.split(":")[0];
const isGithubSource = (s: SourceRow) => s.provider.startsWith("github:");

/** Legend for the ingestion stages, above the activity table. */
function StageLegend() {
  return (
    <div className="src-legend">
      {STAGES.map((st, i) => (
        <Fragment key={st}>
          <span className={`src-legend__stage${i === STAGES.length - 1 ? " src-legend__stage--done" : ""}`}>
            {STAGE_ICONS[i]} {st}
          </span>
          {i < STAGES.length - 1 && <span className="src-legend__line" aria-hidden="true" />}
        </Fragment>
      ))}
    </div>
  );
}

/** Decorative hand-drawn branch in the GitHub panel corner. */
function BranchSketch() {
  return (
    <span className="src-sketch" aria-hidden="true">
      <svg width="150" height="240" viewBox="0 0 150 240" fill="none" stroke="currentColor" strokeWidth="1.1" filter="url(#sketch)">
        <path d="M75 238 C 73 180 76 120 74 60 M74 60 C 60 60 46 44 44 28 M74 80 C 90 78 102 64 104 46 M74 110 C 58 108 48 96 46 84 M74 140 C 92 138 104 126 108 112" />
        <path d="M44 28 c -3 8 2 16 10 18 M104 46 c 4 8 -2 16 -10 16 M46 84 c -2 8 4 14 12 14 M108 112 c 2 10 -6 16 -14 14" />
      </svg>
    </span>
  );
}

function LoadError({ what, onRetry }: { what: string; onRetry: () => void }) {
  return (
    <EmptyState
      icon={<Ic.Globe size={22} />}
      title={`Couldn’t load ${what}`}
      action={<Button compact onClick={onRetry}><Ic.Refresh size={13} /> Retry</Button>}
    >
      The API didn’t answer. If the server is still starting up, retry in a moment.
    </EmptyState>
  );
}

type SourcesTab = "connectors" | "bots";

export default function SourcesPage() {
  const navigate = useNavigate();
  const [tab, setTab] = useState<SourcesTab>("connectors");
  const [showAllActivity, setShowAllActivity] = useState(false);
  const [showAllEvents, setShowAllEvents] = useState(false);
  const [wiz, setWiz] = useState<{ provider: string | null } | null>(null);

  const sources = useSources();
  const catalog = useCatalog();
  const checkpoints = useQuery<any[]>(CHECKPOINTS_QUERY, { map: (d) => d.checkpoints });
  const events = useQuery<any[]>(EVENTS_QUERY, { map: (d) => d.syncEvents });

  const refetchAll = () => { sources.refetch(); catalog.refetch(); checkpoints.refetch(); events.refetch(); };

  const ghSources = (sources.data ?? []).filter(isGithubSource);

  const checkpointRows = checkpoints.data ?? [];
  const visibleCheckpoints = showAllActivity ? checkpointRows : checkpointRows.slice(0, 5);
  const eventRows = events.data ?? [];
  const visibleEvents = showAllEvents ? eventRows : eventRows.slice(0, 5);

  /* ————————————————— page ————————————————— */

  return (
    <>
      <PageHeader
        title="Sources & connectors"
        description="Bring every product conversation into one trusted library."
        actions={
          tab === "connectors" ? (
            <Button variant="primary" onClick={() => setWiz({ provider: null })}>
              <Ic.Plus size={14} /> Add source
            </Button>
          ) : undefined
        }
      />

      <div className="src-tabs">
        <Tabs<SourcesTab>
          value={tab}
          onChange={setTab}
          ariaLabel="Sources sections"
          options={[
            { id: "connectors", label: "Connectors", icon: <Ic.Layers size={14} /> },
            { id: "bots", label: "Bots", icon: <Ic.Chat size={14} /> },
          ]}
        />
      </div>

      {tab === "bots" ? <BotsTab /> : <>

      {/* connector cards */}
      {sources.loading ? (
        <div className="src-loading"><Spinner size="sm" /> Loading sources…</div>
      ) : sources.error || !sources.data ? (
        <div className="card src-panel"><LoadError what="sources" onRetry={sources.refetch} /></div>
      ) : sources.data.length === 0 ? (
        <div className="card src-panel">
          <EmptyState
            icon={<Ic.Layers size={22} />}
            title="No sources connected yet"
            action={<Button variant="primary" compact onClick={() => setWiz({ provider: "github" })}><Ic.Plus size={13} /> Connect GitHub</Button>}
          >
            Connect a GitHub repository to start building the library.
          </EmptyState>
        </div>
      ) : (
        <div className="src-cards">
          {sources.data.map((s) => (
            <ConnectorCard key={s.id ?? s.provider} source={s} onChanged={refetchAll} />
          ))}
        </div>
      )}

      <div className="src-cols">
        {/* ingestion activity */}
        <div className="card src-panel">
          <b className="src-panel__title">Ingestion activity</b>
          <StageLegend />
          {checkpoints.loading ? (
            <div className="src-loading"><Spinner size="sm" /> Loading activity…</div>
          ) : checkpoints.error || !checkpoints.data ? (
            <LoadError what="ingestion activity" onRetry={checkpoints.refetch} />
          ) : checkpointRows.length === 0 ? (
            <EmptyState icon={<Ic.Layers size={22} />}>
              No ingestion activity yet — connect a source to start a backfill.
            </EmptyState>
          ) : (
            <table className="table src-table" style={{ marginTop: 6 }}>
              <colgroup>
                <col style={{ width: "30%" }} /><col style={{ width: "27%" }} /><col style={{ width: "14%" }} /><col style={{ width: "13%" }} /><col style={{ width: "16%" }} />
              </colgroup>
              <thead><tr><th>Source / item</th><th>Progress</th><th>Cursor</th><th>Duration</th><th>Status</th></tr></thead>
              <tbody>
                {visibleCheckpoints.map((r: any) => (
                  <tr key={r.id}>
                    <td><b style={{ fontWeight: 600, fontSize: 13.5 }}>{r.item}</b><span className="fact-src">{PROVIDER_SUB[baseProvider(r.provider)] ?? r.provider}</span></td>
                    <td>
                      <span className="src-prog">
                        <span className="src-prog__track">
                          <span className="src-prog__fill" style={{ width: `${r.total > 0 ? (r.progress / r.total) * 100 : 0}%`, background: r.total > 0 && r.progress === r.total ? "var(--green)" : "var(--blue)" }} />
                        </span>
                        <span className="src-prog__n">{r.progress}/{r.total}</span>
                      </span>
                    </td>
                    <td className="src-mono">{r.cursorId}</td>
                    <td className="card__hint">{r.duration}</td>
                    <td>
                      {r.status === "done"
                        ? <span className="src-status" style={{ color: "var(--green-deep)" }}><Ic.CheckCircle size={13} /> Indexed</span>
                        : r.status === "paused"
                          ? <span className="src-status" style={{ color: "var(--ink-faint)" }}><Ic.Clock size={13} /> Paused</span>
                          : r.status === "error"
                            ? <span className="src-status" style={{ color: "var(--red, #b3452f)" }}><Ic.Close size={13} /> Error</span>
                            : <span className="src-status" style={{ color: "var(--blue)" }}><Ic.Play size={13} /> {r.stage}</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {checkpointRows.length > 0 && (
            <div className="src-panel__foot">
              <span className="card__hint">Showing {visibleCheckpoints.length} of {checkpointRows.length} ingestion tasks · checkpointed &amp; resumable</span>
              {checkpointRows.length > 5 && (
                <button className="linklike src-panel__more" onClick={() => setShowAllActivity((v) => !v)}>
                  {showAllActivity ? "Show fewer" : "View all activity"} <Ic.External size={12} />
                </button>
              )}
            </div>
          )}
        </div>

        {/* github connector detail */}
        <div className="card src-panel src-panel--github">
          <BranchSketch />
          <div className="row" style={{ gap: 9, marginBottom: 12 }}>
            <GitHubMark size={24} />
            <b style={{ fontFamily: "var(--display)", fontSize: 18 }}>GitHub</b>
            {ghSources.length > 0 && <span className="pill pill--running">{ghSources.length} {ghSources.length === 1 ? "repository" : "repositories"}</span>}
          </div>
          {sources.loading ? (
            <div className="src-loading"><Spinner size="sm" /> Loading…</div>
          ) : ghSources.length === 0 ? (
            <EmptyState
              icon={<Ic.Fork size={22} />}
              title="No repositories connected"
              action={<Button variant="primary" compact onClick={() => setWiz({ provider: "github" })}><Ic.Plus size={13} /> Connect repository</Button>}
            >
              Connect a repository and Mari Cloud syncs its Markdown docs — diff-based, so only changed files are re-embedded.
            </EmptyState>
          ) : (
            <>
              {ghSources.map((s) => {
                const c = s.config ?? {};
                return (
                  <div key={s.id} className="src-repo">
                    <div className="src-repo__head">
                      <Ic.Fork size={14} />
                      <b className="src-mono">{c.repo ?? s.name}</b>
                      {c.branch && <Chip tone="faint" caps>{c.branch}</Chip>}
                    </div>
                    <div className="src-repo__meta">
                      <span>Paths <span className="src-mono">{c.paths || "**/*.md + READMEs"}</span></span>
                      <span>Cursor <span className="src-mono">{c.cursor ? String(c.cursor).slice(0, 7) : "—"}</span></span>
                      <span>Last sync {c.last_sync_at ? fmtDateTime(c.last_sync_at) : "never"}</span>
                    </div>
                    {c.last_error && <div className="src-repo__err">Last error: {c.last_error}</div>}
                  </div>
                );
              })}
              <div className="card__hint" style={{ marginTop: 10 }}>
                Changes stream in via the background poll and the <span className="src-mono">POST /webhooks/github</span> push
                endpoint. Sync now / Full resync live on each source card above.
              </div>
            </>
          )}
          <div className="row src-panel__foot" style={{ justifyContent: "flex-start" }}>
            <Button onClick={() => setWiz({ provider: "github" })}><Ic.Plus size={13} /> Connect repository</Button>
            <Button onClick={() => navigate("/audit")}><Ic.ShieldCheck size={13} /> Repository audit</Button>
          </div>
        </div>
      </div>

      <div className="src-cols">
        {/* recent sync events */}
        <div className="card src-panel">
          <b className="src-panel__title">Recent sync events</b>
          {events.loading ? (
            <div className="src-loading"><Spinner size="sm" /> Loading events…</div>
          ) : events.error || !events.data ? (
            <LoadError what="sync events" onRetry={events.refetch} />
          ) : eventRows.length === 0 ? (
            <EmptyState icon={<Ic.Clock size={22} />}>
              No sync events yet — they show up here after the first backfill.
            </EmptyState>
          ) : (
            <table className="table src-table" style={{ marginTop: 6 }}>
              <colgroup>
                <col style={{ width: "16%" }} /><col style={{ width: "24%" }} /><col style={{ width: "34%" }} /><col style={{ width: "26%" }} />
              </colgroup>
              <thead><tr><th>When</th><th>Source</th><th>What</th><th>Details</th></tr></thead>
              <tbody>
                {visibleEvents.map((e: any) => (
                  <tr key={e.id}>
                    <td className="card__hint" style={{ whiteSpace: "nowrap" }}>{fmtDateTime(e.at)}</td>
                    <td><span className="row" style={{ gap: 7, fontSize: 13.5 }}><ProviderMark provider={baseProvider(e.provider)} size={15} /> {PROVIDER_NAME[baseProvider(e.provider)] ?? e.provider}</span></td>
                    <td style={{ fontFamily: "var(--sans)", fontSize: 13 }}>{e.event}</td>
                    <td className="card__hint">{e.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {eventRows.length > 5 && (
            <div className="src-panel__foot">
              <span className="card__hint">Showing {visibleEvents.length} of {eventRows.length} events</span>
              <button className="linklike src-panel__more" onClick={() => setShowAllEvents((v) => !v)}>
                {showAllEvents ? "Show fewer" : "View all events"} <Ic.External size={12} />
              </button>
            </div>
          )}
        </div>

        {/* available connectors — server truth, every one is live */}
        <div className="card src-panel">
          <b className="src-panel__title">Available connectors</b>
          {catalog.loading ? (
            <div className="src-loading"><Spinner size="sm" /> Loading catalog…</div>
          ) : catalog.error || !catalog.data ? (
            <LoadError what="the connector catalog" onRetry={catalog.refetch} />
          ) : (
            <div className="src-avail">
              {catalog.data.map((p) => (
                <div key={p.key} className="src-avail__tile">
                  <div className="src-avail__icon"><ProviderMark provider={p.key} /></div>
                  <div className="src-avail__name">
                    {p.name}
                    {p.connected && <Chip tone="green" caps>Connected</Chip>}
                  </div>
                  <Button compact onClick={() => setWiz({ provider: p.key })}>Add</Button>
                </div>
              ))}
            </div>
          )}
          <div className="src-panel__foot" style={{ justifyContent: "flex-start" }}>
            <span className="card__hint">
              Don’t see a source you need? <a className="linklike" style={{ fontSize: 12.5 }} href="mailto:support@mari.cloud?subject=Connector%20request">Request a connector <Ic.External size={11} /></a>
            </span>
          </div>
        </div>
      </div>

      {/* connector setup wizard */}
      {wiz && (
        <ConnectorWizard
          initialProvider={wiz.provider}
          onClose={() => setWiz(null)}
          onDone={() => { setWiz(null); refetchAll(); }}
        />
      )}

      </>}
    </>
  );
}
