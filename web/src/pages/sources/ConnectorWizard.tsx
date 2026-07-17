// Connector setup wizard — a centered 3-step modal (choose source → configure
// → live sync). The provider list is server truth: GET /connectors/catalog
// (CONNECTORS-CONTRACT.md) — every entry is a real, working connector; there
// is no "coming soon". GitHub keeps its dedicated repo-picker path
// (connectGithubRepo), Upload posts files to /onboard/upload, and every other
// provider renders its credential fields dynamically with an honest
// "Test connection" (POST /connectors/validate) before Connect
// (POST /connectors/connect). Sync progress polls `syncStatus` as before.

import { useEffect, useState } from "react";
import * as Ic from "../../components/icons";
import { Button, Chip, EmptyState, Field, Input, Spinner, Stepper } from "../../components/ui";
import { gql, useQuery } from "../../lib/api";
import {
  CONNECT_BASE, CatalogEntry, DEFAULT_PATHS, ProviderMark, TOP_COUNT,
  connectConnector, fetchCatalog, validateConnector,
} from "./connectors";
import { PHASES, PHASE_LABEL, useSyncStatus } from "./syncStatus";

type GithubRepo = {
  fullName: string;
  description: string;
  private: boolean;
  defaultBranch: string;
  updatedAt: string;
  connected: boolean;
};

const REPOS_QUERY = `{ githubRepos { fullName description private defaultBranch updatedAt connected } }`;

/* ————————————————— step 2 (github): repo picker ————————————————— */

function RepoPicker({ selected, onSelect }: { selected: string | null; onSelect: (repo: string | null) => void }) {
  const [filter, setFilter] = useState("");
  const { data: repos, loading, error, refetch } = useQuery<GithubRepo[]>(REPOS_QUERY, {
    map: (d) => d.githubRepos,
  });

  if (loading) {
    return (
      <div className="wiz-center">
        <Spinner size="sm" /> Loading repositories from GitHub…
      </div>
    );
  }
  if (error || !repos) {
    return (
      <EmptyState
        icon={<Ic.Globe size={22} />}
        title="Couldn’t reach the API"
        action={<Button compact onClick={refetch}><Ic.Refresh size={13} /> Retry</Button>}
      >
        The repository list couldn’t be loaded. Check that the Mari Cloud server is running, then retry.
      </EmptyState>
    );
  }
  if (repos.length === 0) {
    return (
      <EmptyState icon={<Ic.Key size={22} />} title="No GitHub token configured">
        Add <span className="src-mono">MARI_GITHUB_TOKEN</span> (or set <span className="src-mono">github.token</span> in
        the server config) and restart the server — the repositories the token can see will show up here.
      </EmptyState>
    );
  }

  const needle = filter.trim().toLowerCase();
  const shown = needle
    ? repos.filter((r) => r.fullName.toLowerCase().includes(needle) || r.description.toLowerCase().includes(needle))
    : repos;

  return (
    <>
      <Input
        placeholder="Filter repositories…"
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
        aria-label="Filter repositories"
      />
      <div className="wiz-list wiz-list--scroll" role="radiogroup" aria-label="Repositories">
        {shown.length === 0 && <div className="wiz-center card__hint">No repositories match “{filter}”.</div>}
        {shown.map((r) => (
          <label key={r.fullName} className={`wiz-item${r.connected ? " wiz-item--disabled" : ""}`}>
            <input
              type="radio"
              name="wiz-repo"
              disabled={r.connected}
              checked={selected === r.fullName}
              onChange={() => onSelect(r.fullName)}
            />
            <span className="wiz-item__icon"><Ic.Fork size={14} /></span>
            <span className="wiz-repo">
              <span className="wiz-repo__name">
                {r.fullName}
                {r.private && <Chip tone="faint" caps>Private</Chip>}
              </span>
              {r.description && <span className="wiz-repo__desc">{r.description}</span>}
            </span>
            <span className="wiz-item__end">
              {r.connected
                ? <Chip tone="green" caps>Connected</Chip>
                : <span className="card__hint src-mono">{r.defaultBranch}</span>}
            </span>
          </label>
        ))}
      </div>
    </>
  );
}

/* ————————————————— step 2 (upload): file upload ————————————————— */

type UploadResult = { files?: { name: string; docId?: number; chunks?: number; embedded?: number; error?: string }[]; error?: string };

function UploadPanel({ onUploaded }: { onUploaded: () => void }) {
  const [files, setFiles] = useState<FileList | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<UploadResult | null>(null);
  const [failed, setFailed] = useState("");

  const upload = async () => {
    if (!files || files.length === 0 || busy) return;
    setBusy(true);
    setFailed("");
    const form = new FormData();
    Array.from(files).forEach((f) => form.append("files", f));
    try {
      const res = await fetch(`${CONNECT_BASE}/onboard/upload`, { method: "POST", body: form });
      const json = await res.json().catch(() => null);
      if (!res.ok) {
        setFailed(json?.detail || json?.error || `Upload failed (HTTP ${res.status}).`);
      } else {
        setResult(json ?? {});
        onUploaded();
      }
    } catch {
      setFailed("Upload failed — the server didn’t answer.");
    }
    setBusy(false);
  };

  const okFiles = result?.files?.filter((f) => !f.error) ?? [];
  const badFiles = result?.files?.filter((f) => f.error) ?? [];

  return (
    <>
      <Field label="Markdown / text files">
        <input
          type="file"
          multiple
          accept=".md,.mdx,.markdown,.txt"
          aria-label="Files to upload"
          onChange={(e) => { setFiles(e.target.files); setResult(null); setFailed(""); }}
        />
      </Field>
      <span className="card__hint" style={{ display: "block", marginTop: 3 }}>
        Uploaded files land in the <b>Uploads</b> source and are chunked and embedded immediately.
      </span>
      <div style={{ marginTop: 10 }}>
        <Button variant="primary" compact disabled={!files || files.length === 0 || busy} onClick={upload}>
          {busy ? <><Spinner size="sm" /> Uploading…</> : <><Ic.Send size={13} /> Upload {files?.length ? `${files.length} file(s)` : ""}</>}
        </Button>
      </div>
      {failed && <div className="wiz-error" role="alert">{failed}</div>}
      {result && (
        <div className="wiz-done" style={{ marginTop: 10 }}>
          <span className="wiz-auth__ok"><Ic.CheckCircle size={15} /> {okFiles.length} file(s) ingested.</span>
          {badFiles.length > 0 && (
            <div className="wiz-error" role="alert">
              {badFiles.map((f) => <div key={f.name}><b>{f.name}</b>: {f.error}</div>)}
            </div>
          )}
        </div>
      )}
    </>
  );
}

/* ————————————————— step 2 (generic): credential fields ————————————————— */

function ConfigForm({
  entry, config, onChange, testState, onTest,
}: {
  entry: CatalogEntry;
  config: Record<string, string>;
  onChange: (key: string, value: string) => void;
  testState: { busy: boolean; ok: boolean | null; error: string };
  onTest: () => void;
}) {
  return (
    <>
      {entry.fields.map((f) => (
        <div className="wiz-field" key={f.key}>
          <Field label={f.label}>
            <Input
              type={f.secret ? "password" : "text"}
              autoComplete={f.secret ? "new-password" : "off"}
              value={config[f.key] ?? ""}
              placeholder={f.placeholder}
              onChange={(e) => onChange(f.key, e.target.value)}
              aria-label={f.label}
            />
          </Field>
          {f.help && <span className="card__hint" style={{ display: "block", marginTop: 3 }}>{f.help}</span>}
        </div>
      ))}
      {entry.docsUrl && (
        <span className="card__hint" style={{ display: "block", marginTop: 6 }}>
          <a className="linklike" href={entry.docsUrl} target="_blank" rel="noreferrer">
            {entry.name} setup docs <Ic.External size={11} />
          </a>
        </span>
      )}
      <div style={{ marginTop: 12 }}>
        <Button compact disabled={testState.busy} onClick={onTest}>
          {testState.busy ? <><Spinner size="sm" /> Testing…</> : <><Ic.ShieldCheck size={13} /> Test connection</>}
        </Button>
      </div>
      {testState.ok === true && (
        <div className="wiz-done" style={{ marginTop: 8 }}>
          <span className="wiz-auth__ok"><Ic.CheckCircle size={14} /> Connection OK — credentials verified.</span>
        </div>
      )}
      {testState.ok === false && (
        <div className="wiz-error" role="alert">{testState.error || "Validation failed without details."}</div>
      )}
    </>
  );
}

/* ————————————————— step 3: live sync progress ————————————————— */

function SyncProgress({ sourceId, label }: { sourceId: number; label: string }) {
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
      <div className="wiz-center"><Spinner size="sm" /> Starting sync for <b className="src-mono">{label}</b>…</div>
    );
  }

  const running = status.state === "running";
  const failed = status.state === "error";
  const pct = status.total > 0 ? Math.min(100, Math.round((status.done / status.total) * 100)) : 0;

  return (
    <>
      <div className="wiz-phases">
        {PHASES.map((p) => {
          const idx = PHASES.indexOf((status.phase as typeof PHASES[number]) ?? "listing");
          const me = PHASES.indexOf(p);
          const state = !running ? (failed ? "" : "done") : me < idx ? "done" : me === idx ? "active" : "";
          return (
            <span key={p} className={`wiz-phase${state ? ` wiz-phase--${state}` : ""}`}>
              {state === "done" ? <Ic.CheckCircle size={13} /> : state === "active" ? <Spinner size="sm" /> : <Ic.Clock size={13} />}
              {PHASE_LABEL[p]}
            </span>
          );
        })}
      </div>

      {running && (
        <>
          <span className="src-prog" style={{ marginTop: 12 }}>
            <span className="src-prog__track">
              <span className="src-prog__fill" style={{ width: `${pct}%`, background: "var(--blue)" }} />
            </span>
            <span className="src-prog__n">
              {status.total > 0 ? `${status.done}/${status.total} items` : "listing items…"}
            </span>
          </span>
          <div className="card__hint" style={{ marginTop: 8 }}>
            {PHASE_LABEL[status.phase] ?? status.phase} · {status.chunkCount.toLocaleString()} chunks ·{" "}
            {status.embeddedCount.toLocaleString()} embedded
          </div>
        </>
      )}

      {failed && (
        <div className="wiz-error" role="alert">
          <b>Sync failed.</b> {status.lastError || "The server reported an error without details."}
          <div style={{ marginTop: 8 }}>
            <Button compact disabled={retrying} onClick={retry}>
              {retrying ? <><Spinner size="sm" /> Retrying…</> : <><Ic.Refresh size={13} /> Retry sync</>}
            </Button>
          </div>
        </div>
      )}

      {!running && !failed && (
        <div className="wiz-done">
          <span className="wiz-auth__ok"><Ic.CheckCircle size={15} /> <b className="src-mono">{label}</b> is synced.</span>
          <div className="wiz-done__stats">
            {[
              [status.docCount, "documents"],
              [status.chunkCount, "chunks"],
              [status.embeddedCount, "embedded"],
            ].map(([n, label2]) => (
              <span key={label2 as string} className="wiz-done__stat">
                <b>{Number(n).toLocaleString()}</b> {label2}
              </span>
            ))}
          </div>
          {status.cursor && <div className="card__hint">Cursor at <span className="src-mono">{status.cursor}</span> — the scheduled sync flow keeps it fresh.</div>}
        </div>
      )}
    </>
  );
}

/* ————————————————— component ————————————————— */

export function ConnectorWizard({
  initialProvider = null,
  onClose,
  onDone,
}: {
  initialProvider?: string | null;
  onClose: () => void;
  /** Fired when the user finishes (or dismisses) after a source was created. */
  onDone: () => void;
}) {
  const [catalog, setCatalog] = useState<CatalogEntry[] | null>(null);
  const [catalogFailed, setCatalogFailed] = useState(false);
  const [catalogNonce, setCatalogNonce] = useState(0);
  const [showAll, setShowAll] = useState(false);

  const [step, setStep] = useState(initialProvider ? 1 : 0);
  const [provider, setProvider] = useState<string | null>(initialProvider);
  const [repo, setRepo] = useState<string | null>(null);
  const [paths, setPaths] = useState(DEFAULT_PATHS);
  const [config, setConfig] = useState<Record<string, string>>({});
  const [test, setTest] = useState<{ busy: boolean; ok: boolean | null; error: string }>({ busy: false, ok: null, error: "" });
  const [connecting, setConnecting] = useState(false);
  const [connectError, setConnectError] = useState("");
  const [sourceId, setSourceId] = useState<number | null>(null);
  const [uploaded, setUploaded] = useState(false);
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    let alive = true;
    (async () => {
      const cat = await fetchCatalog();
      if (!alive) return;
      if (cat) { setCatalog(cat); setCatalogFailed(false); }
      else setCatalogFailed(true);
    })();
    return () => { alive = false; };
  }, [catalogNonce]);

  const chosen = catalog?.find((p) => p.key === provider) ?? null;
  const isGithub = provider === "github";
  const isUpload = provider === "upload";
  const stepLabels = ["Choose source", isGithub ? "Repository" : isUpload ? "Upload files" : "Configure", "Sync"];

  const requestClose = () => {
    if (connecting) return;
    if (sourceId != null || uploaded) { onDone(); return; } // work done server-side
    if (step > 0) { setConfirming(true); return; }
    onClose();
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (confirming) setConfirming(false);
      else requestClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, connecting, confirming, sourceId, uploaded]);

  const pickProvider = (key: string) => {
    setProvider(key);
    setRepo(null);
    setConfig({});
    setTest({ busy: false, ok: null, error: "" });
    setConnectError("");
  };

  const runTest = async () => {
    if (!provider || test.busy) return;
    setTest({ busy: true, ok: null, error: "" });
    const r = await validateConnector(provider, config);
    if (!r) setTest({ busy: false, ok: false, error: "The server didn’t answer — is the Mari Cloud API running?" });
    else setTest({ busy: false, ok: r.ok, error: r.error });
  };

  const connectGithub = async () => {
    if (!repo || connecting) return;
    setConnecting(true);
    setConnectError("");
    const data = await gql<{ connectGithubRepo: number }>(
      `mutation($repo: String!, $paths: String) { connectGithubRepo(repo: $repo, paths: $paths) }`,
      { repo, paths: paths.trim() || null },
    );
    setConnecting(false);
    if (data && typeof data.connectGithubRepo === "number") {
      setSourceId(data.connectGithubRepo);
      setStep(2);
    } else {
      setConnectError(
        "Connect failed — the server rejected the request. Check that a GitHub token is configured and this repository isn’t already connected.",
      );
    }
  };

  const connectGeneric = async () => {
    if (!provider || connecting) return;
    setConnecting(true);
    setConnectError("");
    const r = await connectConnector(provider, config);
    setConnecting(false);
    if (r && typeof r.sourceId === "number") {
      setSourceId(r.sourceId);
      setStep(2);
    } else {
      setConnectError(r?.error || "Connect failed — the server didn’t answer.");
    }
  };

  const syncLabel = isGithub ? (repo ?? "") : (chosen?.name ?? provider ?? "");

  const catalogGrid = () => {
    if (catalogFailed) {
      return (
        <EmptyState
          icon={<Ic.Globe size={22} />}
          title="Couldn’t load the connector catalog"
          action={<Button compact onClick={() => setCatalogNonce((n) => n + 1)}><Ic.Refresh size={13} /> Retry</Button>}
        >
          GET /connectors/catalog didn’t answer. Check that the Mari Cloud server is running, then retry.
        </EmptyState>
      );
    }
    if (!catalog) return <div className="wiz-center"><Spinner size="sm" /> Loading connector catalog…</div>;
    const shown = showAll ? catalog : catalog.slice(0, TOP_COUNT);
    return (
      <>
        <div className="wiz-grid">
          {shown.map((p) => (
            <button
              key={p.key}
              type="button"
              className={`wiz-tile${provider === p.key ? " is-selected" : ""}`}
              aria-pressed={provider === p.key}
              onClick={() => pickProvider(p.key)}
            >
              <span className="wiz-tile__icon"><ProviderMark provider={p.key} size={24} /></span>
              <span className="wiz-tile__name">
                {p.name}
                {p.connected && <Chip tone="green" caps>Connected</Chip>}
              </span>
              <span className="wiz-tile__desc">{p.blurb}</span>
            </button>
          ))}
        </div>
        {catalog.length > TOP_COUNT && (
          <div style={{ marginTop: 10 }}>
            <button className="linklike" onClick={() => setShowAll((v) => !v)}>
              {showAll ? "Show fewer" : `Show all ${catalog.length} connectors`} <Ic.Chev size={12} />
            </button>
          </div>
        )}
      </>
    );
  };

  const stepBody = () => {
    switch (step) {
      case 0:
        return (
          <>
            <div className="wiz__step-title">1 · Choose source</div>
            <div className="wiz__step-sub">Pick the system Mari Cloud should bring into your knowledge library. Every connector here is live.</div>
            {catalogGrid()}
          </>
        );
      case 1:
        if (isGithub) {
          return (
            <>
              <div className="wiz__step-title">2 · Pick a repository</div>
              <div className="wiz__step-sub">Mari Cloud syncs Markdown docs from the repository, read-only.</div>
              <RepoPicker selected={repo} onSelect={(r) => { setRepo(r); setConnectError(""); }} />
              <div className="wiz-field">
                <Field label="Paths filter (glob)">
                  <Input
                    className="src-input--mono"
                    value={paths}
                    onChange={(e) => setPaths(e.target.value)}
                    placeholder={DEFAULT_PATHS}
                  />
                </Field>
                <span className="card__hint" style={{ display: "block", marginTop: 3 }}>
                  Optional — defaults to <span className="src-mono">{DEFAULT_PATHS}</span> plus the README of every directory.
                </span>
              </div>
              {connectError && <div className="wiz-error" role="alert">{connectError}</div>}
            </>
          );
        }
        if (isUpload) {
          return (
            <>
              <div className="wiz__step-title">2 · Upload files</div>
              <div className="wiz__step-sub">Files are ingested immediately — chunked, hashed, and embedded on the server.</div>
              <UploadPanel onUploaded={() => setUploaded(true)} />
            </>
          );
        }
        return (
          <>
            <div className="wiz__step-title">2 · Configure {chosen?.name ?? provider}</div>
            <div className="wiz__step-sub">{chosen?.blurb || "Credentials stay on the server and are never shown again."}</div>
            {chosen ? (
              <ConfigForm
                entry={chosen}
                config={config}
                onChange={(k, v) => { setConfig((c) => ({ ...c, [k]: v })); setTest({ busy: false, ok: null, error: "" }); setConnectError(""); }}
                testState={test}
                onTest={runTest}
              />
            ) : (
              <div className="wiz-center"><Spinner size="sm" /> Loading provider…</div>
            )}
            {connectError && <div className="wiz-error" role="alert">{connectError}</div>}
          </>
        );
      default:
        return (
          <>
            <div className="wiz__step-title">3 · Sync</div>
            <div className="wiz__step-sub">The initial sync runs on the server — closing this dialog won’t interrupt it.</div>
            {sourceId != null && <SyncProgress sourceId={sourceId} label={syncLabel} />}
          </>
        );
    }
  };

  return (
    <div className="wiz-backdrop" onMouseDown={requestClose}>
      <div className="card wiz" role="dialog" aria-modal="true" aria-label="Connector setup" onMouseDown={(e) => e.stopPropagation()}>
        <span className="wiz__kicker">Connector setup</span>
        <div className="wiz__head">
          <span className="wiz__title">
            {provider && <ProviderMark provider={provider} size={30} />}
            {chosen ? `Connect ${chosen.name}` : "Connect a source"}
          </span>
          <Button icon aria-label="Close connector setup" onClick={requestClose}><Ic.Close size={15} /></Button>
        </div>

        <div className="wiz__steps">
          <Stepper
            labels={stepLabels}
            current={step}
            ariaLabel="Connector setup progress"
            onSelect={(i) => { if (i < step && sourceId == null && !connecting) setStep(i); }}
          />
        </div>

        <div className="wiz__body">
          <div className="card wiz__main">{stepBody()}</div>
          <aside className="wiz__rail">
            <div className="card wiz-railcard">
              <span className="wiz-railcard__title">Connection summary</span>
              {chosen ? (
                <>
                  <div className="row"><ProviderMark provider={chosen.key} size={18} /> <b style={{ fontWeight: 600 }}>{chosen.name}</b></div>
                  {isGithub ? (
                    <>
                      <div className="row" style={{ color: repo ? "var(--ink-soft)" : "var(--ink-faint)" }}>
                        <Ic.Fork size={14} /> {repo ?? "No repository selected"}
                      </div>
                      <div className="row" style={{ color: "var(--ink-soft)" }}>
                        <Ic.Layers size={14} /> <span className="src-mono" style={{ fontSize: 12 }}>{paths.trim() || DEFAULT_PATHS}</span>
                      </div>
                    </>
                  ) : isUpload ? (
                    <div className="row" style={{ color: "var(--ink-soft)" }}><Ic.Send size={14} /> Files land in the Uploads source</div>
                  ) : (
                    chosen.fields.filter((f) => !f.secret && (config[f.key] ?? "").trim()).map((f) => (
                      <div key={f.key} className="row" style={{ color: "var(--ink-soft)" }}>
                        <Ic.Layers size={14} /> <span className="src-mono" style={{ fontSize: 12 }}>{config[f.key]}</span>
                      </div>
                    ))
                  )}
                  {sourceId != null && (
                    <div className="row" style={{ color: "var(--green-deep)" }}><Ic.CheckCircle size={14} /> Source created (#{sourceId})</div>
                  )}
                </>
              ) : (
                <div className="card__hint">Pick a source to begin. Everything stays read-only.</div>
              )}
            </div>
            <div className="card wiz-railcard">
              <span className="wiz-railcard__title">How sync works</span>
              <div className="card__hint">
                Incremental: a per-item content hash means only new or changed items are re-chunked and re-embedded.
                A scheduled sync flow (visible in Flows) keeps every connected source fresh.
              </div>
            </div>
          </aside>
        </div>

        <div className="wiz__foot">
          {confirming ? (
            <div className="wiz-discard" role="alertdialog" aria-label="Discard connector setup?">
              <span>Discard this connector setup?</span>
              <Button compact onClick={() => setConfirming(false)}>Keep editing</Button>
              <Button compact variant="danger" onClick={onClose}>Discard</Button>
            </div>
          ) : step === 0 ? (
            <>
              <Button disabled>{/* keeps footer layout symmetric */}<Ic.ChevL size={13} /> Back</Button>
              <Button variant="primary" disabled={!chosen} onClick={() => setStep(1)}>
                Next <Ic.ChevR size={13} />
              </Button>
            </>
          ) : step === 1 ? (
            <>
              <Button disabled={connecting} onClick={() => setStep(0)}><Ic.ChevL size={13} /> Back</Button>
              {isUpload ? (
                <Button variant="primary" disabled={!uploaded} onClick={onDone}>Done <Ic.CheckCircle size={14} /></Button>
              ) : (
                <Button
                  variant="primary"
                  disabled={connecting || (isGithub ? !repo : !chosen)}
                  onClick={isGithub ? connectGithub : connectGeneric}
                >
                  {connecting ? <><Spinner size="sm" /> Connecting…</> : <>Connect &amp; sync <Ic.ArrowR size={14} /></>}
                </Button>
              )}
            </>
          ) : (
            <>
              <span className="card__hint">Sync continues in the background.</span>
              <Button variant="primary" onClick={onDone}>Done <Ic.CheckCircle size={14} /></Button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
