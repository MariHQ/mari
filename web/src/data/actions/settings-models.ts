/* Settings → Models actions — the embedding model and the LLM provider.
 *
 * Both live in `settings` rows the ingest pipeline reads at runtime, and
 * `updateSetting` replaces a row WHOLE, so each save reads the row back and
 * merges. Writing only the fields the form knows about would drop `options`
 * (the dropdown's own contents) and every key beside it.
 *
 * Chunking has no handler: the table names sources the way the console does,
 * not by the provider key the `chunking` row is stored under, so a save could
 * not address the row it was editing. Test connection has none either: there
 * is no provider-reachability mutation to call. */

import type { SettingsModelsActions } from "@mari-design/components/pages/SettingsModelsPage";
import { gql } from "../../lib/api";
import { mutate } from "./index";

const UPDATE_SETTING = `mutation($key: String!, $value: JSON!) { updateSetting(key: $key, value: $value) }`;
const SETTINGS = `{ settings { key value } }`;
const TEST_GATEWAY = `mutation { testLlmGateway }`;

async function settingRow(key: string): Promise<Record<string, unknown>> {
  const res = await gql<{ settings: { key: string; value: unknown }[] }>(SETTINGS);
  const row = (res?.settings ?? []).find((s) => s.key === key)?.value;
  return row && typeof row === "object" ? { ...(row as Record<string, unknown>) } : {};
}

/** The page's dropdowns speak "provider:model"; the setting stores the two
 *  apart. A value with no provider keeps whatever provider the row had. */
function splitQualified(v: string, fallback: unknown): { provider: unknown; model: string } {
  const i = v.indexOf(":");
  return i === -1
    ? { provider: fallback, model: v }
    : { provider: v.slice(0, i), model: v.slice(i + 1) };
}

function jsonObject(label: string, raw: string): Record<string, unknown> {
  let value: unknown;
  try { value = JSON.parse(raw || "{}"); } catch { throw new Error(`${label} must be valid JSON.`); }
  if (!value || Array.isArray(value) || typeof value !== "object") throw new Error(`${label} must be a JSON object.`);
  return value as Record<string, unknown>;
}

export function settingsModelsActions(): SettingsModelsActions {
  return {
    /* `dims` is null when the model itself changed: vector width is a property
       of the model, and the panel has no way to know the new one's. Null must
       therefore CLEAR the stored width rather than leave the old model's
       number standing over the new model — the row is re-embedded and the
       server writes the real width when it takes the change. A number only
       ever arrives for a model that was not changed, which is the width the
       corpus is already indexed at. */
    saveEmbedding: async ({ model, dims }) => {
      const row = await settingRow("embedding");
      const { provider, model: name } = splitQualified(model, row.provider);
      const { dims: _stale, ...rest } = row;
      await mutate(UPDATE_SETTING, {
        key: "embedding",
        value: { ...rest, provider, model: name, ...(dims === null ? {} : { dims }) },
      });
    },
    saveLlm: async ({ model, openai, anthropic, openaiDirty, anthropicDirty }) => {
      const row = await settingRow("llm");
      const { provider, model: name } = splitQualified(model, row.provider);
      const stored = (row.keys && typeof row.keys === "object" ? row.keys : {}) as Record<string, unknown>;
      const keys = { ...stored };
      // Dirty flags come from the fields themselves. Character inspection is
      // unsafe: a legitimate secret may contain any Unicode character.
      if (openaiDirty) keys.openai = openai;
      if (anthropicDirty) keys.anthropic = anthropic;
      await mutate(UPDATE_SETTING, {
        key: "llm",
        value: { ...row, provider, model: name, keys },
      });
    },
    saveGateway: async (gateway) => {
      const llmRow = await settingRow("llm");
      const embeddingRow = await settingRow("embedding");
      const headers = jsonObject("Routing headers", gateway.headersJson);
      const metadata = jsonObject("Request metadata", gateway.metadataJson);
      const storedGateway = llmRow.gateway && typeof llmRow.gateway === "object"
        ? llmRow.gateway as Record<string, unknown> : {};
      await mutate(UPDATE_SETTING, {
        key: "llm",
        value: {
          ...llmRow, provider: "gateway", model: gateway.generationModel.trim(),
          gateway: {
            ...storedGateway, base_url: gateway.baseUrl.trim(), token: gateway.token,
            headers, metadata, model_header: gateway.modelHeader.trim(), max_retries: gateway.maxRetries,
          },
        },
      });
      const { dims: _oldDims, ...embeddingRest } = embeddingRow;
      await mutate(UPDATE_SETTING, { key: "embedding", value: {
        ...embeddingRest, provider: "gateway", model: gateway.embeddingModel.trim(),
      } });
    },
    testGateway: async () => {
      const result = await mutate(TEST_GATEWAY);
      const health = result?.testLlmGateway as { ok?: boolean; detail?: string; models?: number; latency_ms?: number } | undefined;
      return { ok: Boolean(health?.ok), text: health?.detail ?? "Gateway health check returned no result." };
    },
  };
}
