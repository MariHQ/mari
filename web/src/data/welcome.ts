/* Welcome (onboarding) adapter.
 *
 * The wizard is a sequence of steps this app does not route between yet, so
 * this fills the content each step reads — the connector catalog, the repos a
 * token can see, the glossary candidates a scan proposed, the sync rows — and
 * opens on the hero. Credential FIELDS come from the catalog; credential
 * VALUES never leave the server (CONNECTORS-CONTRACT.md). */

import type { CField, Repo, Tile, UploadedFile, WelcomeData } from "@mari-design/components/pages/WelcomePage";
import type { Candidate } from "@mari-design/components/features/WelcomeGlossaryStep";
import type { SyncRow } from "@mari-design/components/features/WelcomeSyncPanel";
import { useEffect, useMemo } from "react";
import { useQuery } from "../lib/api";
import type { PageData } from "./types";

/* ── query ──────────────────────────────────────────────────────────────── */

const QUERY = `{
  connectorCatalog
  githubRepos { fullName description private defaultBranch connected }
  glossaryCandidates { id term variants definition evidence }
  uploadManifest { summary files { name detail } }
  settings { key value }
}`;

// Source discovery can involve a remote knowledge substrate. Keep it out of
// the query that renders the setup controls: an unavailable or slow Onyx
// instance must not prevent an administrator from opening the screen where
// they can repair its configuration.
const SYNC_QUERY = `{
  sourcePulse { id provider name status docsCount health kind lastSyncAt }
}`;

type Res = {
  connectorCatalog: {
    key: string; name: string; blurb: string; connected?: boolean;
    docsUrl?: string;
    fields: { key: string; label: string; secret?: boolean; placeholder?: string; help?: string; multiline?: boolean; required?: boolean }[];
  }[];
  githubRepos: { fullName: string; description: string; private: boolean; defaultBranch: string; connected: boolean }[];
  glossaryCandidates: { id: number; term: string; variants: string; definition: string; evidence: string; evidenceDocId?: number }[];
  sourcePulse?: {
    id: number; provider: string; name: string; status: string;
    docsCount: number; health: string; kind: string; lastSyncAt: string;
  }[];
  uploadManifest: { summary: string; files: { name: string; detail: string }[] } | null;
  settings: { key: string; value: unknown }[];
};

/* ── mapping helpers ────────────────────────────────────────────────────── */

/** Credential fields for one provider, as specs. `value` is deliberately
 *  absent: the catalog endpoint returns what to ask for, never what is stored. */
function fieldsFor(res: Res, key: string): CField[] {
  const provider = (res.connectorCatalog ?? []).find((p) => p.key === key);
  return (provider?.fields ?? []).map<CField>((f) => ({
    key: f.key,
    label: f.label,
    secret: f.secret,
    multiline: f.multiline,
    placeholder: f.placeholder || undefined,
    help: f.help || undefined,
    required: f.required,
  }));
}

/* The finish step's table speaks the four-state sync vocabulary. A source the
   ingest side calls anything else has not reported a failure, which is what
   "done" says about a source that already carries documents. */
const SYNC_STATE: Record<string, SyncRow["state"]> = {
  healthy: "done", ok: "done",
  running: "syncing", syncing: "syncing",
  failed: "error", error: "error",
  paused: "queued",
};

export function mapSyncRows(res: Res): SyncRow[] {
  return (res.sourcePulse ?? []).map<SyncRow>((s) => ({
    id: String(s.id),
    // Sources qualify one provider instance as `github:owner/repo` or
    // `website:docs.example.com`; onboarding selects the catalog key.
    // Normalize the live row so the just-connected tile can find it.
    provider: (s.provider || s.kind || "").split(":", 1)[0],
    name: s.name,
    state: SYNC_STATE[(s.health || s.status || "").toLowerCase()] ?? "queued",
    docCount: s.docsCount,
    // "" is the server's "never synced" — the row must not print an epoch.
    lastSyncAt: s.lastSyncAt || null,
    // done/total, chunk and embedded counts are live ingest state, one query
    // per source; the row draws the counts it was given and no progress bar.
  }));
}

/** What the Upload connector actually ingested, per file: the chunk/embedded
 *  breakdown the server counts off `chunks` at read time. */
export function mapUploadFiles(res: Res): UploadedFile[] {
  return (res.uploadManifest?.files ?? []).map<UploadedFile>((f) => ({
    name: f.name, detail: f.detail,
  }));
}

/** The row the `connect-syncing` step watches. Nothing is being connected
 *  during a page load, so it names no provider and reports no progress. */
const NO_CONNECT_SYNC: SyncRow = { id: "", provider: "", name: "", state: "queued" };

/* ── mapper ─────────────────────────────────────────────────────────────── */

export const EMPTY: WelcomeData = {
  step: "hero",
  knowledgeSubstrate: {
    provider: "native", url: "", apiKeySet: false, apiKeyHint: "",
    timeoutSeconds: 30, searchMode: "keyword",
  },
  tiles: [], connectorCount: 0,
  repos: [], selectedRepo: "", pathsGlob: "",
  slackFields: [], notionFields: [], gdriveFields: [],
  uploadSummary: "", uploadFiles: [],
  connectSync: NO_CONNECT_SYNC,
  glossaryCandidates: [], syncRows: [],
  doneSummary: { sourcesSynced: 0, glossaryTerms: 0 },
};

/** Pure: the whole response → everything the wizard renders. */
export function buildWelcome(res: Res | null): WelcomeData {
  if (!res) return EMPTY;
  const catalog = res.connectorCatalog ?? [];
  const syncRows = mapSyncRows(res);
  const synced = syncRows.filter((r) => r.state === "done").length;
  const terms = (res.glossaryCandidates ?? []).length;
  const substrateRow = (res.settings ?? []).find((row) => row.key === "knowledge_substrate")?.value;
  const substrate = substrateRow && typeof substrateRow === "object" ? substrateRow as Record<string, unknown> : {};

  return {
    // The wizard opens where it opens; stepping through it is routing this app
    // does not have yet.
    step: "hero",
    knowledgeSubstrate: {
      provider: substrate.provider === "onyx" ? "onyx" : "native",
      url: String(substrate.url || ""),
      apiKeySet: Boolean(substrate.api_key_set),
      apiKeyHint: String(substrate.api_key_hint || ""),
      timeoutSeconds: Math.max(1, Math.min(120, Number(substrate.timeout_seconds) || 30)),
      searchMode: substrate.search_mode === "agentic" ? "agentic" : "keyword",
    },
    tiles: catalog.map<Tile>((p) => ({
      key: p.key, name: p.name, blurb: p.blurb, connected: p.connected,
      docsUrl: p.docsUrl || undefined,
      fields: fieldsFor(res, p.key),
    })),
    connectorCount: catalog.length,
    repos: (res.githubRepos ?? []).map<Repo>((r) => ({
      name: r.fullName, desc: r.description, priv: r.private, branch: r.defaultBranch,
    })),
    // Which repo and which glob: choices the user has not made yet.
    selectedRepo: "",
    pathsGlob: "",
    slackFields: fieldsFor(res, "slack"),
    notionFields: fieldsFor(res, "notion"),
    gdriveFields: fieldsFor(res, "gdrive"),
    // The Upload connector's manifest: one row per file, with the chunk and
    // embedded counts read off `chunks`. "" and [] for a workspace that has
    // uploaded nothing — never a sample receipt.
    uploadSummary: res.uploadManifest?.summary ?? "",
    uploadFiles: mapUploadFiles(res),
    connectSync: NO_CONNECT_SYNC,
    glossaryCandidates: (res.glossaryCandidates ?? []).map<Candidate>((c) => ({
      id: c.id,
      term: c.term,
      definition: c.definition,
      // The document the harvester found the term in. "" for a term someone
      // typed in by hand, which was never mined from a document.
      evidence: c.evidence ?? "",
      evidenceDocId: c.evidenceDocId || undefined,
    })),
    syncRows,
    // Counted off the rows above, so the closing line cannot claim more than
    // the table shows.
    doneSummary: { sourcesSynced: synced, glossaryTerms: terms },
  };
}

/* ── adapter ────────────────────────────────────────────────────────────── */

export function useWelcome(): PageData<WelcomeData> {
  const q = useQuery<Res>(QUERY);
  const sync = useQuery<Pick<Res, "sourcePulse">>(SYNC_QUERY);
  const data = useMemo(() => buildWelcome(q.data ? {
    ...q.data,
    sourcePulse: sync.data?.sourcePulse ?? [],
  } : null), [q.data, sync.data]);
  const refetchSync = sync.refetch;
  // A connection starts a background ingest after this page's initial read.
  // Keep the remaining onboarding steps live so the just-added source moves
  // from queued/syncing to its actual terminal state without leaving setup.
  useEffect(() => {
    // Only the small progress query is polled. Reissuing the full onboarding
    // query on this cadence can cancel a slower first response forever, which
    // leaves the entire page stuck on its loading skeleton.
    const timer = window.setInterval(refetchSync, 2_000);
    return () => window.clearInterval(timer);
  }, [refetchSync]);
  return {
    data,
    loading: q.loading,
    error: q.error ? (q.errorText ?? "Setup is temporarily unavailable.") : null,
  };
}
