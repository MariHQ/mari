/* Chrome adapter — the data the frame shows around EVERY page.
 *
 * `PageFrame` draws a notification bell and a global-search overlay on all 25
 * pages, and the app was handing it neither: `chrome.notifications` and
 * `chrome.recentSearches` were never populated, so the bell was a permanent
 * zero everywhere and the overlay opened with nothing behind it. Both have a
 * real source, so this is a query rather than a hidden control.
 *
 * It is a page adapter in every way except that its "page" is the frame: one
 * document, one mapper, mounted once per route by App.tsx. */

import type { ShellNotification } from "@mari-design/components/pages/PageFrame";
import { useQuery } from "../lib/api";
import { useAuth } from "../lib/auth";

const QUERY = `{
  notifications { id kind text detail at read }
  recentSearches(limit: 6)
}`;

type Res = {
  notifications: {
    id: number; kind: string; text: string; detail: string; at: string; read: boolean;
  }[];
  recentSearches: string[];
};

export type Chrome = {
  notifications: ShellNotification[];
  recentSearches: string[];
};

/** Nothing fetched yet. Empty, not invented: an empty bell is the honest
 *  reading of "we have not been told about anything". */
const EMPTY: Chrome = { notifications: [], recentSearches: [] };

export function mapChrome(res: Res): Chrome {
  return {
    notifications: (res.notifications ?? []).map<ShellNotification>((n) => ({
      id: String(n.id),
      title: n.text,
      body: n.detail,
      // `at` is the label the writer recorded (notifications.at_label). It is
      // already relative prose ("2h ago"), which is what the bell renders; the
      // row carries no timestamp to re-format.
      time: n.at,
      unread: !n.read,
    })),
    // The queries this workspace has actually run, newest first. The overlay
    // shows them before anything is typed; a workspace that has never searched
    // gets none rather than a suggested search nobody made.
    recentSearches: res.recentSearches ?? [],
  };
}

export function useChrome(): Chrome {
  const { user } = useAuth();
  /* Signed out there is nobody to have notifications, so nothing is asked.
     This used to send `{ __typename }` instead — a query with no answer to
     collect, which still POSTed to /graphql and still needed a session, so
     every signed-out visitor's first act was a 401 on the sign-in screen. */
  const q = useQuery<Chrome>(user ? QUERY : null, { map: mapChrome });
  // No loading or error surface: the frame has nowhere to put either, and a
  // failed bell must never take a working page down with it.
  return q.data ?? EMPTY;
}
