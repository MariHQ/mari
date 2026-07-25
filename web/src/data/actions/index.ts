/* Page actions: the write half of the app.
 *
 * `src/data/<page>.ts` maps a query onto a page's `data`. `actions/<page>.ts`
 * maps the app's side effects onto that page's `actions` — one handler per
 * intent the user has ("approve", "setDue"), never named for the transport.
 *
 * Contract, matching `PageProps.actions` in the library:
 *   - handlers may be async and THROW on failure; the page shows the message,
 *     so a failed write is exactly as visible as a failed read;
 *   - a page with no entry here simply gets no actions and keeps the local
 *     behaviour the library ships. Adding a write must never be what makes a
 *     control respond at all.
 *
 * Each factory takes `refresh` (re-read `/auth/me`, used by the auth screens)
 * and returns that page's actions object.
 */

import { gqlResult, clearQueryCache } from "../../lib/api";

import { answersActions } from "./answers";
import { auditActions } from "./audit";
import { decisionsActions } from "./decisions";
import { docReviewActions } from "./doc-review";
import { factsActions } from "./facts";
import { flowsActions } from "./flows";
import { insightsActions } from "./insights";
import { knowledgeActions } from "./knowledge";
import { lineageActions } from "./lineage";
import { libraryActions } from "./library";
import { loginActions } from "./login";
import { preferencesActions } from "./preferences";
import { publishActions } from "./publish";
import { settingsApiKeysActions } from "./settings-api-keys";
import { settingsAuditLogActions } from "./settings-audit-log";
import { settingsDesignActions } from "./settings-design";
import { settingsGeneralActions } from "./settings-general";
import { settingsMembersActions } from "./settings-members";
import { settingsModelsActions } from "./settings-models";
import { setupActions } from "./setup";
import { sourcesActions } from "./sources";
import { tasksActions } from "./tasks";
import { welcomeActions } from "./welcome";

/** Run a mutation, surface the real server message, then invalidate reads so
    the next render shows the new truth instead of the cached old one. */
export async function mutate(query: string, variables?: Record<string, unknown>): Promise<any> {
  const r = await gqlResult(query, variables);
  if (!r.ok) throw new Error(r.error);
  clearQueryCache();
  return r.data;
}

/** What the app can give a page's handlers. A context object rather than
    positional args: the library keeps emitting intents ("go to /sources") and
    this is where they become real routing, so this list will grow. */
export type ActionContext = {
  /** Re-read /auth/me; the auth screens route off the result. */
  refresh: () => Promise<void> | void;
  /** Follow an in-app href. The library must never navigate itself — a
      component doing `window.location.href = "/"` hard-reloads the SPA and
      hardcodes one app's URL scheme into a shared component. */
  navigate: (href: string) => void;
};

/** Page id -> factory. Ids match `PAGES[].id` in the library. */
export const ACTION_FACTORIES: Record<string, (ctx: ActionContext) => unknown> = {
  answers: answersActions,
  audit: auditActions,
  decisions: decisionsActions,
  "doc-review": docReviewActions,
  facts: factsActions,
  flows: flowsActions,
  insights: insightsActions,
  knowledge: knowledgeActions,
  lineage: lineageActions,
  library: libraryActions,
  login: loginActions,
  preferences: preferencesActions,
  publish: publishActions,
  "settings-api-keys": settingsApiKeysActions,
  "settings-audit-log": settingsAuditLogActions,
  "settings-design": settingsDesignActions,
  "settings-general": settingsGeneralActions,
  "settings-members": settingsMembersActions,
  "settings-models": settingsModelsActions,
  setup: setupActions,
  sources: sourcesActions,
  tasks: tasksActions,
  welcome: welcomeActions,
};
