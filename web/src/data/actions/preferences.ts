/* Preferences writes.
 *
 * All three act on the session's own account — no user id crosses the wire, so
 * there is nothing to tamper with. Each throws the server's message on
 * failure, which the page renders through the same surface a failed read uses:
 * "That is not your current password" reaches the person who typed it.
 */

import type { PreferencesActions, NotificationPrefs } from "@mari-design/components/pages/PreferencesPage";
import { authPost } from "../../lib/auth";

/* Two intents stay unwired, and both controls are therefore not drawn:
 *
 *   • `language`. `/auth/preferences` stores a name, a timezone and three
 *     notification switches; there is no language on the record, so the page
 *     is sent none and draws no language select. A select that reset on every
 *     reload is a control that does nothing.
 *   • `deleteAccount`. Nothing in `/auth` closes an account. The card is drawn
 *     only where a handler exists, so an app that cannot delete an account
 *     does not show a button claiming it can. */

export function preferencesActions({ refresh }: { refresh: () => Promise<void> | void }): PreferencesActions {
  return {
    // `language` is never received: the page only sends what it collected, and
    // it collects none for the reason above.
    saveProfile: async ({ name, timezone }: { name: string; timezone: string }) => {
      await authPost("/auth/preferences/profile", { name, timezone });
      // The display name and initials are in the topbar, so the session has to
      // be re-read or the chrome keeps showing the old name until a reload.
      await refresh();
    },
    changePassword: async ({ current, next }: { current: string; next: string }) => {
      await authPost("/auth/preferences/password", { current, next });
    },
    setNotification: async (key: keyof NotificationPrefs, on: boolean) => {
      await authPost("/auth/preferences/notification", { key, on });
    },
  };
}
