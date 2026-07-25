/* Settings → Access log actions.
 *
 * The log is immutable, so neither intent here is a write. `refresh` goes and
 * looks again; `filter` narrows what the SERVER is asked for — the log is
 * thousands of rows deep and this page holds a window of 200, so a filter that
 * only narrowed the window would search the most recent events and present
 * that as the answer.
 */

import type { SettingsAuditLogActions } from "@mari-design/components/pages/SettingsAuditLogPage";
import { clearQueryCache } from "../../lib/api";
import type { ActionContext } from "./index";

export function settingsAuditLogActions({ navigate }: ActionContext): SettingsAuditLogActions {
  return {
    refresh: () => {
      // The adapters hold their reads in a session cache, so dropping it is
      // what makes the next render fetch. A route re-entry then re-runs the
      // query and paints the new window.
      clearQueryCache();
      window.location.reload();
    },

    /* The filter lives in the route, so a narrowed log is a link someone can
       send and a reload does not quietly widen it again. An empty field drops
       its parameter rather than sending "", which is not the same question. */
    filter: ({ query, from, to }) => {
      const next = new URLSearchParams();
      if (query.trim()) next.set("q", query.trim());
      if (from) next.set("from", from);
      if (to) next.set("to", to);
      const qs = next.toString();
      navigate(qs ? `/settings/audit?${qs}` : "/settings/audit");
    },
  };
}
