import type { SourcesBotsActions } from "@mari-design/components/features/SourcesBots";
import { authenticatedFetch, gqlResult, invalidateQueries, projectHeaders } from "../../lib/api";
import { mutate } from "./index";

const UPDATE_SETTING = `mutation($key: String!, $value: JSON!) { updateSetting(key: $key, value: $value) }`;

/** Everything in a bot's settings row except the masked, derived secret
 * indicators. updateSetting replaces the row, so status metadata must survive
 * a credential rotation. */
async function botRow(key: "github_bot"): Promise<Record<string, unknown>> {
  const res = await gqlResult<{ settings: { key: string; value: unknown }[] }>(`{ settings { key value } }`);
  if (!res.ok) throw new Error(res.error);
  const row = (res.data?.settings ?? []).find((s) => s.key === key)?.value;
  if (!row || typeof row !== "object") return {};
  return Object.fromEntries(
    Object.entries(row as Record<string, unknown>).filter(([keyName]) =>
      !keyName.endsWith("_set") && !keyName.endsWith("_hint")),
  );
}

async function postBot<T>(path: string, body: Record<string, unknown> = {}): Promise<T> {
  const res = await authenticatedFetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...projectHeaders() },
    body: JSON.stringify(body),
  });
  const json = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(typeof (json as any)?.detail === "string"
      ? (json as any).detail : `The server answered HTTP ${res.status}.`);
  }
  return json as T;
}

/** Bot setup actions are destination actions. Kept as a small composable
 * factory while Sources still accepts the old contract, so there is one
 * implementation during the route transition and no credential drift. */
export function botActions(): SourcesBotsActions {
  return {
    loadSlackManifest: async () => {
      const response = await authenticatedFetch("/bots/slack/manifest", {
        headers: projectHeaders(),
      });
      if (!response.ok) throw new Error(`The server answered HTTP ${response.status}.`);
      return response.text();
    },
    saveSlackCredentials: async ({ botToken, appToken, signingSecret }) => {
      await postBot("/bots/slack/setup", {
        bot_token: botToken.trim(),
        app_token: appToken.trim(),
        signing_secret: signingSecret.trim(),
      });
      invalidateQueries();
    },
    testSlackConnection: async () => {
      const result = await postBot<{ ok: boolean; team?: string; error?: string }>("/bots/slack/test");
      if (result.ok) invalidateQueries();
      return { ok: result.ok, teamName: result.team || undefined, error: result.error };
    },
    saveGithubWebhookSecret: async (secret: string) => {
      await mutate(UPDATE_SETTING, {
        key: "github_bot",
        value: { ...(await botRow("github_bot")), webhook_secret: secret.trim() },
      });
    },
  };
}
