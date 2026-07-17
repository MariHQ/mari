// Auth context: cookie-session state fetched from /auth/me, shared app-wide.

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { clearQueryCache } from "./api";

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
  refresh: () => Promise<void>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue>({
  user: null,
  needsSetup: false,
  bypassEnabled: false,
  oauth: { github: false, google: false },
  loading: true,
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
  const refresh = useCallback((): Promise<void> => {
    return fetch("/auth/me")
      .then(async (res) => {
        if (!res.ok) { applyUser(null); return; }
        const data = await res.json();
        applyUser(data.user ?? null);
        setNeedsSetup(Boolean(data.needsSetup));
        setBypassEnabled(Boolean(data.bypassEnabled));
        setOauth({ github: Boolean(data.oauth?.github), google: Boolean(data.oauth?.google) });
      })
      .catch(() => applyUser(null))
      .finally(() => setLoading(false));
  }, [applyUser]);

  const logout = useCallback(async () => {
    try {
      await fetch("/auth/logout", { method: "POST" });
    } catch {
      /* the refresh below reflects whatever state the server is in */
    }
    await refresh();
  }, [refresh]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <AuthContext.Provider value={{ user, needsSetup, bypassEnabled, oauth, loading, refresh, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
