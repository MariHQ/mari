/* Sources & connectors adapter.
 *
 * `connectorCatalog` exposes the same catalog as the REST connector endpoint,
 * so the console and ingestion surface cannot disagree about what is wired.
 * Bot delivery belongs to Destinations and is deliberately not queried here. */

import type {
  Connector, FirstSync, SourcesData,
} from "@mari-design/components/pages/SourcesPage";
import type { Source, SyncState, Tier } from "@mari-design/components/features/SourcesConnectorCard";
import type { WizardProviderSpec } from "@mari-design/components/features/SourcesConnectorWizard";
import type { PropertyItem } from "@mari-design/components";
import { useQuery } from "../lib/api";
import type { PageData } from "./types";

/* ── query ──────────────────────────────────────────────────────────────── */

const QUERY = `{
  sourcePulse {
    id provider name status docsCount health kind lastSyncAt bars
    syncIntervalMinutes syncFlowId config
  }
  connectorCatalog
}`;

type Res = {
  sourcePulse: {
    id: number; provider: string; name: string; status: string; docsCount: number;
    health: string; kind: string; lastSyncAt: string; bars: number[];
    syncIntervalMinutes: number | null; syncFlowId: number | null;
    /* The MASKED config (connector_sync.masked_config): non-secret values in
       the clear, secret values as "••••••", internal maps dropped. Plus
       runtime keys like last_error that are state, not settings. */
    config: { last_error?: string; [key: string]: unknown } | null;
  }[];
  connectorCatalog: {
    key: string; name: string; blurb: string; docsUrl?: string; connected?: boolean;
    fields: { key: string; label: string; secret?: boolean; placeholder?: string; help?: string; multiline?: boolean; required?: boolean }[];
  }[];
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

/** The stored settings the edit-connection dialog may prefill: exactly what
 *  the API reported, scalar values only, coerced to the strings the form
 *  holds. Secrets arrive already masked and the dialog never prefills a
 *  masked value, so nothing here decides what is safe to show. */
function configOf(cfg: Record<string, unknown> | null): Record<string, string> | undefined {
  if (!cfg) return undefined;
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(cfg)) {
    if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") out[k] = String(v);
  }
  return out;
}

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
      lastError: s.config?.last_error || undefined,
      config: configOf(s.config),
      /* A source's cadence is the trigger of the "Sync <name>" flow the engine
         creates alongside it, which is why it can be absent in two different
         ways and the card treats them differently:

           no flow  → `undefined`, and no schedule control is drawn at all;
           a paused or manual-only flow → `null`, "manual only", which is what
           that flow actually does.

         Collapsing the two would put a select reading "Manual" over a source
         whose schedule nobody has ever been able to state. */
      syncIntervalMinutes: s.syncFlowId == null ? undefined : s.syncIntervalMinutes,
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
      multiline: f.multiline, required: f.required,
    })),
  }));
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
  summary: [],
};

/** Pure: the whole response → everything the Sources grid renders. */
export function buildSources(res: Res | null): SourcesData {
  if (!res) return EMPTY;
  const sources = mapSources(res);
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
