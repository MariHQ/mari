// Auth context: cookie-session state fetched from /auth/me, shared app-wide.

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { clearQueryCache, setSessionRecovery } from "./api";

export type AuthUser = {
  id: number | string;
  name: string;
  email: string;
  role: string;
  initials: string;
  tint: number;
  provider: string;
};

export type OAuthAvailability = { github: boolean; google: boolean };

type AuthContextValue = {
  user: AuthUser | null;
  needsSetup: boolean;
  bypassEnabled: boolean;
  oauth: OAuthAvailability;
  loading: boolean;
  /** Why the app could not sign the visitor in on its own, verbatim from the
   *  server. Only ever set on a bypass-enabled deployment whose /auth/bypass
   *  refused — a silent failure there would be a blank sign-in screen with no
   *  account to sign into. */
  error: string | null;
  refresh: () => Promise<void>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue>({
  user: null,
  needsSetup: false,
  bypassEnabled: false,
  oauth: { github: false, google: false },
  loading: true,
  error: null,
  refresh: async () => {},
  logout: async () => {},
});

/** POST JSON to an /auth endpoint; throws Error(detail) on non-2xx. */
export async function authPost<T = any>(path: string, body: Record<string, unknown>): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const json = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(typeof json?.detail === "string" ? json.detail : "Something went wrong. Please try again.");
  }
  return json as T;
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [needsSetup, setNeedsSetup] = useState(false);
  const [bypassEnabled, setBypassEnabled] = useState(false);
  const [oauth, setOauth] = useState<OAuthAvailability>({ github: false, google: false });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Last seen session identity — when it changes (sign-in, sign-out, user
  // switch) every cached query result belongs to someone else: drop it.
  const lastUserId = useRef<AuthUser["id"] | null>(null);
  const applyUser = useCallback((next: AuthUser | null) => {
    const nextId = next?.id ?? null;
    if (lastUserId.current !== nextId) {
      lastUserId.current = nextId;
      clearQueryCache();
    }
    setUser(next);
  }, []);

  // No async/await here: state updates live in .then callbacks so the initial
  // mount effect never sets state synchronously (react-hooks/set-state-in-effect),
  // same style as useQuery's effect in api.ts.
  //
  // Resolves to the two facts a caller has to weigh together — who we are, and
  // whether this server offers the sign-in bypass — because reading them off
  // state instead would read the render before this one.
  const load = useCallback((): Promise<{ user: AuthUser | null; bypassEnabled: boolean }> => {
    return fetch("/auth/me")
      .then((res) => (res.ok ? res.json() : null))
      .catch(() => null)
      .then((data: any) => {
        const next = (data?.user ?? null) as AuthUser | null;
        applyUser(next);
        setNeedsSetup(Boolean(data?.needsSetup));
        setBypassEnabled(Boolean(data?.bypassEnabled));
        setOauth({ github: Boolean(data?.oauth?.github), google: Boolean(data?.oauth?.google) });
        return { user: next, bypassEnabled: Boolean(data?.bypassEnabled) };
      });
  }, [applyUser]);

  const refresh = useCallback((): Promise<void> => {
    return load().then(() => setLoading(false));
  }, [load]);

  /* Demo deployments sign the visitor in rather than showing a wall.
   *
   * `bypassEnabled` is the SERVER's setting, and when it is on, an
   * unauthenticated POST /auth/bypass already returns a workspace-admin
   * session to anyone who asks — the button on the sign-in screen is one
   * click over exactly this call. Making the app place that call itself
   * grants nothing that was not already public; it removes a click on a
   * deployment whose whole purpose is to be walked into. Where the setting is
   * off, /auth/me reports false, none of this runs, and the ordinary login
   * flow is untouched.
   *
   * At most one attempt per page load, whatever the outcome:
   *   • a bypass that fails must not be retried in a loop, and
   *   • signing out must not sign you straight back in. */
  const autoSignInUsed = useRef(false);
  const autoSignIn = useCallback((): Promise<boolean> => {
    return load().then(({ user: found, bypassEnabled: offered }) => {
      if (found) return true;
      if (!offered || autoSignInUsed.current) return false;
      autoSignInUsed.current = true;
      return authPost("/auth/bypass", {})
        .then(() => load().then(({ user: signedIn }) => Boolean(signedIn)))
        .catch((e: unknown) => {
          // The demo could not let them in. Say why, in the server's words, on
          // the sign-in screen — the alternative is a login form for a
          // workspace whose credentials the visitor was never given.
          setError(e instanceof Error ? e.message : "Could not start a demo session.");
          return false;
        });
    });
  }, [load]);

  const logout = useCallback(async () => {
    try {
      await fetch("/auth/logout", { method: "POST" });
    } catch {
      /* the refresh below reflects whatever state the server is in */
    }
    await refresh();
  }, [refresh]);

  // One boot per page load. Without it React 18's development double-invoke
  // runs two attempts, and the one that loses the race resolves false and
  // drops `loading` while the other is still signing in — which is the /login
  // flash this is here to prevent.
  const booted = useRef(false);
  useEffect(() => {
    if (booted.current) return;
    booted.current = true;
    // `loading` stays true across the auto sign-in, so the gate in App.tsx
    // renders nothing until the session question is settled. Letting it fall
    // false first would redirect to /login and then yank the visitor back —
    // the sign-in screen as a flash of the wrong page.
    void autoSignIn().then(() => setLoading(false));
  }, [autoSignIn]);

  /* A read that comes back 401 is holding a cookie this server does not
     accept (see api.ts). Re-resolving the session is the honest recovery: on
     an ordinary deployment it just confirms we are signed out, and the 401
     reaches the page. */
  useEffect(() => {
    setSessionRecovery(autoSignIn);
    return () => setSessionRecovery(null);
  }, [autoSignIn]);

  return (
    <AuthContext.Provider value={{ user, needsSetup, bypassEnabled, oauth, loading, error, refresh, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
