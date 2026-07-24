/* Page actions: the write half of the app.
 *
 * `src/data/<page>.ts` maps a query onto a page's `data`. This maps the app's
 * side effects onto a page's `actions` — one handler per intent the page
 * offers, named for what the user is doing, never for the transport.
 *
 * Handlers throw on failure and the page shows it, so a failed write is as
 * visible as a failed read. After a successful write the relevant query cache
 * is dropped so the next render reflects the new truth rather than the stale
 * one it already had.
 *
 * A page with no entry here simply gets no actions, and its controls keep the
 * local-state behaviour the library ships. That is a deliberate floor: adding
 * a write must never be what makes a button respond at all.
 */

import type { Credentials, LoginActions } from "@mari-design/components/pages/LoginPage";
import { authPost } from "../../lib/auth";

/* ── login ──────────────────────────────────────────────────────────────── */

/** `onDone` re-reads /auth/me so the session gate re-evaluates and routes on. */
export function loginActions({ refresh, navigate }: {
  refresh: () => Promise<void> | void;
  navigate: (href: string) => void;
}): LoginActions {
  return {
    signIn: async ({ email, password }: Credentials) => {
      await authPost("/auth/login", { email, password });
      await refresh();
    },
    register: async ({ name, email, password, workspace }: Credentials) => {
      await authPost("/auth/register", { name, email, password, workspace });
      await refresh();
    },
    magicLink: async (email: string) => {
      // Always reports success, by design: the endpoint will not say whether
      // an address has an account, because that is an enumeration oracle.
      await authPost("/auth/magic-link", { email });
      // The confirmation is a route, so a reload does not silently undo it and
      // "Back to sign in" has something to return from.
      navigate(`/login?sent=${encodeURIComponent(email)}`);
    },
    backToSignIn: () => navigate("/login"),
    bypass: async () => {
      // The server decides whether this is allowed; it 404s when the bypass is
      // off, and the page only offers the button when /auth/me says it is on.
      await authPost("/auth/bypass", {});
      await refresh();
    },
    oauth: (provider: "github" | "google" | "sso") => {
      // A full navigation, not fetch: the provider has to render its consent
      // screen and redirect back with the callback code.
      window.location.href = `/auth/oauth/${provider}`; // full navigation: the provider renders its own consent screen
    },
  };
}
