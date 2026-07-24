/* Sources & connectors adapter.
 *
 * Three backends meet here, and two of them only existed over REST before this
 * page: `connectorCatalog` and `botsStatus` are new GraphQL fields that call
 * the very same `/connectors/catalog` and `/bots/status` functions, so the
 * console and the REST surface can never disagree about what is wired up. */

import type {
  Connector, FirstSync, SourcesData,
} from "@mari-design/components/pages/SourcesPage";
import type { Source, SyncState, Tier } from "@mari-design/components/features/SourcesConnectorCard";
import type { WizardProviderSpec } from "@mari-design/components/features/SourcesConnectorWizard";
import type { GithubStatus, SlackStatus } from "@mari-design/components/features/SourcesBots";
import type { PropertyItem } from "@mari-design/components";
import { useQuery } from "../lib/api";
import type { PageData } from "./types";

/* ── query ──────────────────────────────────────────────────────────────── */

const QUERY = `{
  sourcePulse { id provider name status docsCount health kind lastSyncAt bars }
  connectorCatalog
  botsStatus
}`;

type Res = {
  sourcePulse: {
    id: number; provider: string; name: string; status: string; docsCount: number;
    health: string; kind: string; lastSyncAt: string; bars: number[];
  }[];
  connectorCatalog: {
    key: string; name: string; blurb: string; docsUrl?: string; connected?: boolean;
    fields: { key: string; label: string; secret?: boolean; placeholder?: string; help?: string; multiline?: boolean }[];
  }[];
  botsStatus: {
    slack: { configured: boolean; teamName: string; lastEventAt: string | null; lastError: string | null };
    github: { webhookConfigured: boolean; lastDeliveryAt: string | null; sources: { id: number; repo: string }[] };
  } | null;
};

/* ── mapping helpers ────────────────────────────────────────────────────── */

/* The card's three-tier honesty model. `kind` tells the two apart: a source a
   connector owns can report live phase and counts; a row the onboarding seeded
   can only show the document count it was given, and must not draw a
   last-sync line it does not have. */
const tierOf = (kind: string): Tier =>
  kind === "github" || kind === "connector" || kind === "upload" ? "live" : "legacy";

/* sources.health is a display word the ingest side writes. The card has four
   states and colors each one; anything unrecognized reads as healthy, which is
   what a source nobody has reported a problem about is. */
const STATE: Record<string, SyncState> = {
  healthy: "healthy", ok: "healthy",
  running: "running", syncing: "running",
  failed: "failed", error: "failed",
  paused: "paused",
};

export function mapSources(res: Res): Source[] {
  return (res.sourcePulse ?? []).map<Source>((s) => {
    const tier = tierOf(s.kind);
    return {
      id: String(s.id),
      provider: s.provider,
      name: s.name,
      tier,
      state: STATE[(s.health || s.status || "").toLowerCase()] ?? "healthy",
      // Live phase, done/total and chunk counts come from the in-memory
      // ingest registry via `syncStatus(sourceId)` — one query per source,
      // which this page does not make on load. The card falls back to the
      // stored document count, which is what `legacy` means.
      docsCount: s.docsCount,
      // "" is the server's "never synced"; the card must not print an epoch.
      lastSyncAt: s.lastSyncAt || null,
      // [] when a source has had no recent document changes — never a curve.
      bars: s.bars ?? [],
    };
  });
}

export function mapCatalog(res: Res): WizardProviderSpec[] {
  return (res.connectorCatalog ?? []).map<WizardProviderSpec>((p) => ({
    key: p.key,
    name: p.name,
    blurb: p.blurb,
    docsUrl: p.docsUrl || undefined,
    connected: p.connected,
    // Field SPECS only — the catalog endpoint never returns stored values.
    fields: (p.fields ?? []).map((f) => ({
      key: f.key, label: f.label, secret: f.secret,
      placeholder: f.placeholder || undefined, help: f.help || undefined,
      multiline: f.multiline,
    })),
  }));
}

export function mapBots(res: Res): { slack: SlackStatus; github: GithubStatus } {
  const b = res.botsStatus;
  return {
    slack: {
      configured: Boolean(b?.slack?.configured),
      teamName: b?.slack?.teamName || undefined,
      lastEventAt: b?.slack?.lastEventAt || undefined,
      lastError: b?.slack?.lastError || undefined,
    },
    github: {
      webhookConfigured: Boolean(b?.github?.webhookConfigured),
      lastDeliveryAt: b?.github?.lastDeliveryAt || undefined,
      repos: (b?.github?.sources ?? []).map((s) => s.repo).filter(Boolean),
    },
  };
}

/** The standalone first-sync panel reports on one source a connect flow just
 *  created. Nothing has been connected during a page load, so it reports on
 *  nothing — every counter zero, no provider, no error. */
const NO_FIRST_SYNC: FirstSync = {
  provider: "", name: "", phase: "", done: 0, total: 0,
  docCount: 0, chunkCount: 0, embeddedCount: 0, lastSyncAt: "", error: "",
};

/* ── mapper ─────────────────────────────────────────────────────────────── */

export const EMPTY: SourcesData = {
  view: "grid", sources: [], catalog: [], connector: null, connectPhase: "configure",
  uploadFiles: [], syncPhase: "queued", firstSync: NO_FIRST_SYNC,
  slack: { configured: false }, github: { webhookConfigured: false, repos: [] },
  summary: [],
};

/** Pure: the whole response → everything the Sources grid renders. */
export function buildSources(res: Res | null): SourcesData {
  if (!res) return EMPTY;
  const sources = mapSources(res);
  const bots = mapBots(res);
  const catalog = mapCatalog(res);

  // Rail facts, counted off the same rows the grid draws.
  const summary: PropertyItem[] = sources.length
    ? [
        { label: "Connected", value: String(sources.length) },
        { label: "Documents", value: sources.reduce((n, s) => n + (s.docsCount ?? 0), 0).toLocaleString("en-US") },
        { label: "Failing", value: String(sources.filter((s) => s.state === "failed").length) },
      ]
    : [];

  return {
    // The grid. The inline connect flow, the wizard and the standalone
    // sync-status screen are routes this app does not have yet, so nothing
    // claims a provider is halfway through being set up.
    view: "grid",
    sources,
    catalog,
    connector: null as Connector | null,
    connectPhase: "configure",
    uploadFiles: [],
    syncPhase: "queued",
    firstSync: NO_FIRST_SYNC,
    slack: bots.slack,
    github: bots.github,
    summary,
  };
}

/* ── adapter ────────────────────────────────────────────────────────────── */

export function useSources(): PageData<SourcesData> {
  const q = useQuery<SourcesData>(QUERY, { map: buildSources });
  return {
    data: q.data ?? EMPTY,
    loading: q.loading,
    error: q.error ? (q.errorText ?? "Sources are temporarily unavailable.") : null,
  };
}
