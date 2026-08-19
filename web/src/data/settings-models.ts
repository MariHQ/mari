/* Settings → Models adapter — the embedding model, the LLM provider and its
 * keys, and per-source chunking.
 *
 * All four live in `settings` rows the ingest pipeline reads at runtime, so
 * this page shows exactly what the server will use on the next sync. Provider
 * keys come back masked ("••••…last4"); the console never holds the secret. */

import type { SettingsModelsData } from "@mari-design/components/pages/SettingsModelsPage";
import type { ChunkRow, GatewaySettings, ProviderKeys } from "@mari-design/components/features/SettingsModelsConfig";
import type { PropertyItem } from "@mari-design/components";
import { useMemo } from "react";
import { useQuery } from "../lib/api";
import type { PageData } from "./types";

/* ── query ──────────────────────────────────────────────────────────────── */

const QUERY = `{
  settings { key value }
  indexStats
  sourcePulse { provider name }
}`;

type Res = {
  settings: { key: string; value: unknown }[];
  indexStats: { docs: number; chunks: number; embedded: number } | null;
  sourcePulse: { provider: string; name: string }[];
};

type EmbeddingRow = { provider?: string; model?: string; dims?: number; options?: string[] };
type GatewayRow = { base_url?: string; token?: string; headers?: Record<string, string>; metadata?: Record<string, unknown>; model_header?: string; max_retries?: number };
type LlmRow = { provider?: string; model?: string; options?: string[]; keys?: { openai?: string; anthropic?: string }; gateway?: GatewayRow };
type ChunkSpec = { strategy?: string; max_tokens?: number; overlap?: number };

/* ── mapping helpers ────────────────────────────────────────────────────── */

function row<T>(res: Res, key: string): T {
  const found = (res.settings ?? []).find((s) => s.key === key);
  return (found && typeof found.value === "object" && found.value !== null ? found.value : {}) as T;
}

/** The stored setting is `{provider, model}`; the dropdowns and `options` list
 *  both speak "provider:model", so that is the value the page shows. */
const qualified = (r: { provider?: string; model?: string }): string =>
  r.model ? (r.provider ? `${r.provider}:${r.model}` : r.model) : "";

/** `chunking` is keyed by source provider, plus a `default` fallback. The
 *  table names the source the way the rest of the console does. */
export function mapChunking(res: Res): ChunkRow[] {
  const chunking = row<Record<string, ChunkSpec>>(res, "chunking");
  const displayName = new Map((res.sourcePulse ?? []).map((s) => [s.provider, s.name]));
  return Object.entries(chunking)
    .filter(([, spec]) => spec && typeof spec === "object")
    .map<ChunkRow>(([key, spec]) => ({
      source: key === "default" ? "Default" : (displayName.get(key) ?? key),
      strategy: spec.strategy ?? "",
      max_tokens: spec.max_tokens ?? 0,
      overlap: spec.overlap ?? 0,
    }));
}

const NO_KEYS: ProviderKeys = { openai: "", anthropic: "" };
const gatewayOf = (llm: LlmRow, embedding: EmbeddingRow): GatewaySettings => ({
  baseUrl: llm.gateway?.base_url ?? "",
  token: llm.gateway?.token ?? "",
  generationModel: llm.provider === "gateway" ? (llm.model ?? "") : "",
  embeddingModel: embedding.provider === "gateway" ? (embedding.model ?? "") : "",
  headersJson: JSON.stringify(llm.gateway?.headers ?? {}, null, 2),
  metadataJson: JSON.stringify(llm.gateway?.metadata ?? {}, null, 2),
  modelHeader: llm.gateway?.model_header ?? "",
  maxRetries: llm.gateway?.max_retries ?? 2,
});
const withGateway = (options: string[], fallback: string) =>
  options.some((option) => option.startsWith("gateway:")) ? options : [...options, fallback];

/* ── mapper ─────────────────────────────────────────────────────────────── */

export const EMPTY: SettingsModelsData = {
  phase: "config",
  embedding: "", llm: "", dims: 0,
  embeddingOptions: [], llmOptions: [], chunking: [], keys: NO_KEYS,
  gateway: gatewayOf({}, {}),
  indexSummary: "", testOk: "", testError: "", summary: [],
};

/** Pure: the whole response → everything the page renders. */
export function buildSettingsModels(res: Res | null): SettingsModelsData {
  if (!res) return EMPTY;
  const embedding = row<EmbeddingRow>(res, "embedding");
  const llm = row<LlmRow>(res, "llm");
  const idx = res.indexStats;
  const n = (v: number) => v.toLocaleString("en-US");
  const indexSummary = idx ? `${n(idx.docs)} documents · ${n(idx.embedded)} of ${n(idx.chunks)} chunks embedded` : "";

  return {
    // The edit / test / saved steps are lifecycle a mutation drives. A page
    // that was just loaded is showing the configuration, nothing more.
    phase: "config",
    embedding: qualified(embedding),
    llm: qualified(llm),
    dims: embedding.dims ?? 0,
    embeddingOptions: withGateway(embedding.options ?? [], "gateway:enterprise-embedding"),
    llmOptions: withGateway(llm.options ?? [], "gateway:enterprise-chat"),
    chunking: mapChunking(res),
    // Masked by the server (queries.py `_mask_setting`); "" means no key set,
    // which is what the field should read as.
    keys: { openai: llm.keys?.openai ?? "", anthropic: llm.keys?.anthropic ?? "" },
    gateway: gatewayOf(llm, embedding),
    indexSummary,
    // Connection tests run against a provider and produce their result then.
    // Nothing has been tested on a plain page load, so there is no outcome to
    // report in either direction.
    testOk: "",
    testError: "",
    summary: idx
      ? ([
          { label: "Documents", value: n(idx.docs) },
          { label: "Chunks embedded", value: `${n(idx.embedded)} of ${n(idx.chunks)}` },
          ...(embedding.dims ? [{ label: "Vector dimensions", value: String(embedding.dims) }] : []),
        ] as PropertyItem[])
      : [],
  };
}

/* ── adapter ────────────────────────────────────────────────────────────── */

export function useSettingsModels(): PageData<SettingsModelsData> {
  const q = useQuery<Res>(QUERY, { map: (d: Res) => d });
  /* Built once per RESPONSE, not once per render. SettingsModelsConfig's
     sentinel compares `keys` (an object) and `chunking` (an array) by
     identity, so a fresh build per render would wipe the API-key and
     chunk-size fields the user is typing into. */
  const data = useMemo(() => buildSettingsModels(q.data), [q.data]);
  return {
    data,
    loading: q.loading,
    error: q.error ? (q.errorText ?? "Model configuration is temporarily unavailable.") : null,
  };
}
