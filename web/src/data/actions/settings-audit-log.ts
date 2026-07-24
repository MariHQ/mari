/* Settings → Access log actions.
 *
 * The log is immutable, so the one intent this page has is "go and look
 * again". There is no mutation behind it and there should not be: `refresh`
 * drops the cached read and re-enters the route, which is exactly what the
 * button claims to do. */

import type { SettingsAuditLogActions } from "@mari-design/components/pages/SettingsAuditLogPage";
import { clearQueryCache } from "../../lib/api";

export function settingsAuditLogActions(): SettingsAuditLogActions {
  return {
    refresh: () => {
      // The adapters hold their reads in a session cache, so dropping it is
      // what makes the next render fetch. A route re-entry then re-runs the
      // query and paints the new window.
      clearQueryCache();
      window.location.reload();
    },
  };
}
