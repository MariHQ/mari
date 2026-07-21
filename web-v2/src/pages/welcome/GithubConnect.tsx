// GitHub connect drawer — the real repo-picker → connectGithubRepo → live
// sync flow, same queries and mutation as the Sources ConnectorWizard
// (githubRepos, connectGithubRepo, syncStatus polling via SyncPanel).

import { useState } from "react";
import * as Ic from "../../components/icons";
import { GitHubMark } from "../../components/icons";
import { Button, Chip, Drawer, EmptyState, Field, Input, Spinner } from "../../components/ui";
import { gql, useQuery } from "../../lib/api";
import { DEFAULT_PATHS } from "../sources/connectors";
import type { SessionConnection } from "./catalog";
import { SyncPanel } from "./SyncPanel";

type GithubRepo = {
  fullName: string;
  description: string;
  private: boolean;
  defaultBranch: string;
  updatedAt: string;
  connected: boolean;
};

const REPOS_QUERY = `{ githubRepos { fullName description private defaultBranch updatedAt connected } }`;

function RepoPicker({ selected, onSelect }: { selected: string | null; onSelect: (repo: string) => void }) {
  const [filter, setFilter] = useState("");
  const { data: repos, loading, error, refetch } = useQuery<GithubRepo[]>(REPOS_QUERY, { map: (d) => d.githubRepos });

  if (loading) return <div className="wc-center"><Spinner size="sm" /> Loading repositories from GitHub…</div>;
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
        Add <span className="mono">MARI_GITHUB_TOKEN</span> (or set <span className="mono">github.token</span> in the
        server config) and restart the server — the repositories the token can see will show up here.
      </EmptyState>
    );
  }

  const needle = filter.trim().toLowerCase();
  const shown = needle
    ? repos.filter((r) => r.fullName.toLowerCase().includes(needle) || r.description.toLowerCase().includes(needle))
    : repos;

  return (
    <>
      <Input placeholder="Filter repositories…" value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter repositories" />
      <div className="wc-repos" role="radiogroup" aria-label="Repositories">
        {shown.length === 0 && <div className="wc-center card__hint">No repositories match “{filter}”.</div>}
        {shown.map((r) => (
          <label key={r.fullName} className={`wc-repo${r.connected ? " wc-repo--disabled" : ""}`}>
            <input
              type="radio"
              name="wc-repo"
              disabled={r.connected}
              checked={selected === r.fullName}
              onChange={() => onSelect(r.fullName)}
            />
            <span className="wc-repo__icon"><Ic.Fork size={14} /></span>
            <span className="wc-repo__body">
              <span className="wc-repo__name">{r.fullName}{r.private && <Chip tone="faint" caps>Private</Chip>}</span>
              {r.description && <span className="wc-repo__desc">{r.description}</span>}
            </span>
            <span className="wc-repo__end">
              {r.connected ? <Chip tone="green" caps>Connected</Chip> : <span className="card__hint mono">{r.defaultBranch}</span>}
            </span>
          </label>
        ))}
      </div>
    </>
  );
}

export function GithubConnect({ onClose, onConnected }: {
  onClose: () => void;
  onConnected: (c: SessionConnection) => void;
}) {
  const [repo, setRepo] = useState<string | null>(null);
  const [paths, setPaths] = useState(DEFAULT_PATHS);
  const [connecting, setConnecting] = useState(false);
  const [error, setError] = useState("");
  const [sourceId, setSourceId] = useState<number | null>(null);

  const connect = async () => {
    if (!repo || connecting) return;
    setConnecting(true);
    setError("");
    const data = await gql<{ connectGithubRepo: number }>(
      `mutation($repo: String!, $paths: String) { connectGithubRepo(repo: $repo, paths: $paths) }`,
      { repo, paths: paths.trim() || null },
    );
    setConnecting(false);
    if (data && typeof data.connectGithubRepo === "number") {
      setSourceId(data.connectGithubRepo);
      onConnected({ provider: "github", name: "GitHub", sourceId: data.connectGithubRepo, detail: repo, polls: true });
    } else {
      setError("Connect failed — the server rejected the request. Check that a GitHub token is configured and this repository isn’t already connected.");
    }
  };

  return (
    <Drawer
      open
      onOpenChange={(o) => { if (!o && !connecting) onClose(); }}
      title={<span className="wc-drawer-title"><GitHubMark size={18} /> Connect GitHub</span>}
      width={620}
      footer={
        <div className="wc-foot">
          {sourceId == null ? (
            <>
              <span className="card__hint">Mari Cloud syncs Markdown docs read-only.</span>
              <Button variant="primary" disabled={!repo || connecting} onClick={connect}>
                {connecting ? <><Spinner size="sm" /> Connecting…</> : <>Connect &amp; sync <Ic.ArrowR size={14} /></>}
              </Button>
            </>
          ) : (
            <>
              <span className="card__hint">Sync continues on the server — closing won’t interrupt it.</span>
              <Button variant="primary" onClick={onClose}>Done <Ic.CheckCircle size={14} /></Button>
            </>
          )}
        </div>
      }
    >
      {sourceId == null ? (
        <>
          <p className="wc-drawer-sub">Pick a repository — repos come straight from the GitHub token the server is configured with.</p>
          <RepoPicker selected={repo} onSelect={(r) => { setRepo(r); setError(""); }} />
          <div className="wc-field">
            <Field label="Paths filter (glob)" hint={`Optional — defaults to ${DEFAULT_PATHS} plus the README of every directory.`}>
              <Input className="mono" value={paths} onChange={(e) => setPaths(e.target.value)} placeholder={DEFAULT_PATHS} />
            </Field>
          </div>
          {error && <div className="wc-error" role="alert">{error}</div>}
        </>
      ) : (
        <>
          <p className="wc-drawer-sub">The initial sync runs on the server — live status below.</p>
          <SyncPanel sourceId={sourceId} label={repo ?? "repository"} />
        </>
      )}
    </Drawer>
  );
}
