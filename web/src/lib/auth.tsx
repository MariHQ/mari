// Auth context: cookie-session state fetched from /auth/me, shared app-wide.

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { clearQueryCache, projectHeaders, setActiveProject, setSessionRecovery } from "./api";

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
export type AuthProject = { id: number; slug: string; name: string; role: string; capabilities: string[] };

type AuthContextValue = {
  user: AuthUser | null;
  needsSetup: boolean;
  bypassEnabled: boolean;
  registrationEnabled: boolean;
  oauth: OAuthAvailability;
  projects: AuthProject[];
  activeProject: AuthProject | null;
  selectProject: (project: AuthProject) => Promise<void>;
  loading: boolean;
  refresh: () => Promise<void>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue>({
  user: null,
  needsSetup: false,
  bypassEnabled: false,
  registrationEnabled: false,
  oauth: { github: false, google: false },
  projects: [],
  activeProject: null,
  selectProject: async () => {},
  loading: true,
  refresh: async () => {},
  logout: async () => {},
});

/** POST JSON to an /auth endpoint; throws Error(detail) on non-2xx. */
export async function authPost<T = any>(path: string, body: Record<string, unknown>): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...projectHeaders() },
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
  const [registrationEnabled, setRegistrationEnabled] = useState(false);
  const [oauth, setOauth] = useState<OAuthAvailability>({ github: false, google: false });
  const [projects, setProjects] = useState<AuthProject[]>([]);
  const [active, setActive] = useState<AuthProject | null>(null);
  const [loading, setLoading] = useState(true);

  // Last seen session identity — when it changes (sign-in, sign-out, user
  // switch) every cached query result belongs to someone else: drop it.
  const lastUserId = useRef<AuthUser["id"] | null>(null);
  const lastUser = useRef<AuthUser | null>(null);
  const lastBypassEnabled = useRef(false);
  const applyUser = useCallback((next: AuthUser | null) => {
    const nextId = next?.id ?? null;
    if (lastUserId.current !== nextId) {
      lastUserId.current = nextId;
      clearQueryCache();
    }
    lastUser.current = next;
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
    return fetch("/auth/me", { headers: projectHeaders() })
      .then((res) => {
        if (res.status === 401 || res.status === 403) return null;
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((data: any) => {
        const next = (data?.user ?? null) as AuthUser | null;
        applyUser(next);
        setNeedsSetup(Boolean(data?.needsSetup));
        setBypassEnabled(Boolean(data?.bypassEnabled));
        setRegistrationEnabled(Boolean(data?.registrationEnabled));
        lastBypassEnabled.current = Boolean(data?.bypassEnabled);
        setOauth({ github: Boolean(data?.oauth?.github), google: Boolean(data?.oauth?.google) });
        const available = (data?.projects ?? []) as AuthProject[];
        const selected = (data?.activeProject ?? null) as AuthProject | null;
        setProjects(available);
        setActive(selected);
        if (selected) {
          setActiveProject(selected.slug || selected.id);
          localStorage.setItem("mari.project", selected.slug || String(selected.id));
        }
        return { user: next, bypassEnabled: Boolean(data?.bypassEnabled) };
      })
      // A transport failure is not evidence that the cookie stopped being a
      // valid session. Keep the last confirmed identity and cached workspace
      // data; a later request/retry can resolve the outage without bouncing a
      // signed-in person through the login wall.
      .catch(() => ({ user: lastUser.current, bypassEnabled: lastBypassEnabled.current }));
  }, [applyUser]);

  const refresh = useCallback((): Promise<void> => {
    return load().then(() => setLoading(false));
  }, [load]);

  /* There is deliberately no automatic POST /auth/bypass here. A demo server
   * (`bypassEnabled`) offers a one-click way in, but it is a button on the
   * sign-in screen, not something the app does to a visitor on arrival —
   * every deployment shows the wall first, and signing out lands on it and
   * stays there. The server enforces the same line: with no cookie presented
   * it resolves nobody, bypass or not (see server/auth.py). */
  const logout = useCallback(async () => {
    try {
      await fetch("/auth/logout", { method: "POST", headers: projectHeaders() });
    } catch {
      /* the refresh below reflects whatever state the server is in */
    }
    await refresh();
  }, [refresh]);

  const selectProject = useCallback(async (project: AuthProject) => {
    setActiveProject(project.slug || project.id);
    localStorage.setItem("mari.project", project.slug || String(project.id));
    clearQueryCache();
    await refresh();
  }, [refresh]);

  useEffect(() => {
    const stored = localStorage.getItem("mari.project");
    if (stored) setActiveProject(stored);
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  /* A read that comes back 401 is holding a cookie this server does not
     accept (see api.ts). Re-resolving the session is the honest recovery: it
     either finds the session is actually fine (retry the read) or confirms we
     are signed out, in which case `user` drops to null and the gate routes to
     /login instead of a page rendering its 401. */
  const recheck = useCallback((): Promise<boolean> => {
    return load().then(({ user: found }) => Boolean(found));
  }, [load]);
  useEffect(() => {
    setSessionRecovery(recheck);
    return () => setSessionRecovery(null);
  }, [recheck]);

  return (
    <AuthContext.Provider value={{ user, needsSetup, bypassEnabled, registrationEnabled, oauth, projects, activeProject: active, selectProject, loading, refresh, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
