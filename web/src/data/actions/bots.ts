import type { SourcesBotsActions } from "@mari-design/components/features/SourcesBots";
import { gqlResult } from "../../lib/api";
import { mutate } from "./index";

const UPDATE_SETTING = `mutation($key: String!, $value: JSON!) { updateSetting(key: $key, value: $value) }`;

/** Everything in a bot's settings row except the masked, derived secret
 * indicators. updateSetting replaces the row, so status metadata must survive
 * a credential rotation. */
async function botRow(key: "slack_bot" | "github_bot"): Promise<Record<string, unknown>> {
  const res = await gqlResult<{ settings: { key: string; value: unknown }[] }>(`{ settings { key value } }`);
  if (!res.ok) throw new Error(res.error);
  const row = (res.data?.settings ?? []).find((s) => s.key === key)?.value;
  if (!row || typeof row !== "object") return {};
  return Object.fromEntries(
    Object.entries(row as Record<string, unknown>).filter(([keyName]) =>
      !keyName.endsWith("_set") && !keyName.endsWith("_hint")),
  );
}

async function postBot<T>(path: string): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
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
    saveSlackCredentials: async ({ botToken, signingSecret }) => {
      await mutate(UPDATE_SETTING, {
        key: "slack_bot",
        value: { ...(await botRow("slack_bot")), bot_token: botToken, signing_secret: signingSecret.trim() },
      });
    },
    testSlackConnection: async () => {
      const result = await postBot<{ ok: boolean; team?: string; error?: string }>("/bots/slack/test");
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
