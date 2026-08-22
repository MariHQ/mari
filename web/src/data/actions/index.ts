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

import { gqlResult, invalidateQueries } from "../../lib/api";

import { auditActions } from "./audit";
import { decisionsActions } from "./decisions";
import { docReviewActions } from "./doc-review";
import { factsActions } from "./facts";
import { insightsActions } from "./insights";
import { knowledgeActions } from "./knowledge";
import { lineageActions } from "./lineage";
import { overviewActions } from "./overview";
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
import { workflowsActions } from "./workflows";
import { welcomeActions } from "./welcome";

/** Run a mutation, surface the real server message, then invalidate reads so
    the next render shows the new truth instead of the cached old one. */
export async function mutate(query: string, variables?: Record<string, unknown>): Promise<any> {
  const r = await gqlResult(query, variables);
  if (!r.ok) throw new Error(r.error);
  invalidateQueries();
  return r.data;
}

/** What the app can give a page's handlers. A context object rather than
    positional args: the library keeps emitting intents ("go to /sources") and
    this is where they become real routing, so this list will grow. */
export type ActionContext = {
  /** Last confirmed signed-in display name, for ownership defaults. */
  currentUserName: string;
  /** Re-read /auth/me; the auth screens route off the result. */
  refresh: () => Promise<void> | void;
  /** Follow an in-app href. The library must never navigate itself — a
      component doing `window.location.href = "/"` hard-reloads the SPA and
      hardcodes one app's URL scheme into a shared component. */
  navigate: (href: string) => void;
  /** Same, but replacing the current entry instead of pushing a new one. For
      state that lives in the URL because it must be shareable, not because
      every keystroke of it is a place to go back to (a search box). */
  replace: (href: string) => void;
};

/** Page id -> factory. Ids match `PAGES[].id` in the library. */
export const ACTION_FACTORIES: Record<string, (ctx: ActionContext) => unknown> = {
  audit: auditActions,
  decisions: decisionsActions,
  "doc-review": docReviewActions,
  facts: factsActions,
  insights: insightsActions,
  knowledge: knowledgeActions,
  lineage: lineageActions,
  overview: overviewActions,
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
  welcome: welcomeActions,
  workflows: workflowsActions,
};
