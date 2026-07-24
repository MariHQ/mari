/* Settings → API keys actions.
 *
 * `createApiKey` returns the secret, once. That value is handed straight to
 * the page, which shows it through TokenReveal and then forgets it: it is
 * never stored here, never logged, and there is no read path that could fetch
 * it again — the server keeps a hash. Revocation is destructive and the page
 * routes it through <ConfirmButton>. */

import type { SettingsApiKeysActions } from "@mari-design/components/pages/SettingsApiKeysPage";
import { mutate } from "./index";

const CREATE = `mutation($name: String!, $scopes: String!) {
  createApiKey(name: $name, scopes: $scopes)
}`;
const REVOKE = `mutation($id: Int!) { revokeApiKey(id: $id) }`;

export function settingsApiKeysActions(): SettingsApiKeysActions {
  return {
    createKey: async ({ name, scopes }) => {
      const d = await mutate(CREATE, { name, scopes });
      const secret = d?.createApiKey;
      if (typeof secret !== "string" || !secret) {
        // Better to say the key cannot be shown than to render an empty token
        // box the user would copy and paste into a config.
        throw new Error("The key was created but the server returned no secret to show.");
      }
      return secret;
    },
    revokeKey: async (id) => {
      await mutate(REVOKE, { id });
    },
  };
}
