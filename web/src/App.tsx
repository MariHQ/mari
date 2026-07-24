import { useMemo } from "react";
import { BrowserRouter, Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { PAGES, type PageModule } from "@mari-design/components/pages";
import { landingPageFor } from "@mari-design/components/pages/PageFrame";
import { NavProvider } from "@mari-design/components/navigation/Link";
import { adapterFor } from "./data";
import { SEARCH_SCOPES, globalSearch } from "./data/search";
import { ACTION_FACTORIES } from "./data/actions";
import { AuthProvider, useAuth } from "./lib/auth";
import { useIsMobile } from "./lib/mobile";

/* The whole console, routed off the component library's own page registry.
 *
 * There is no page code in this app. `PAGES` supplies the route, the title and
 * the component; `src/data/` supplies the data. Adding a page to the library
 * adds it here — the only local step is writing its adapter. */

/** Ids that must stay reachable without a session. */
const PUBLIC = new Set(["login", "setup", "welcome"]);

/** Builds one route component per page, at module load, so the adapter hook is
 *  a fixed call for that component and never varies between renders. */
function routeFor(page: PageModule<any, any>) {
  const Page = page.component;
  const useData = adapterFor(page.id);
  const makeActions = ACTION_FACTORIES[page.id];

  return function PageRoute() {
    const { data, loading, error } = useData();
    const { user, refresh, logout } = useAuth();
    const navigate = useNavigate();
    // Rebuilt only when `refresh` changes, so a page never sees a new actions
    // object on every render (which would defeat memoised children).
    const actions = useMemo(
      () => makeActions?.({ refresh, navigate: (href: string) => navigate(href) }),
      [refresh, navigate],
    );
    // The real session drives the topbar. The frame names the signed-out state
    // itself when this is absent; it never invents a person.
    const chrome = useMemo(() => ({
      user: user
        ? { name: user.name, initials: user.initials, detail: user.role === "admin" ? "Admin" : user.role }
        : undefined,
      onSignOut: logout,
      // The sidebar emits a nav id; the registry knows that id's route. Going
      // through PAGES rather than a second hand-written map means a page can
      // never appear in the menu without a destination.
      // The sidebar emits a NAV id, which is not always a page id: five
      // Settings pages share the "settings" nav item, so looking the nav id up
      // directly found nothing and Settings went nowhere. The library resolves
      // a nav id to the page it should land on; the registry knows its route.
      onNavigate: (id: string) => {
        const target = PAGES.find((p) => p.id === landingPageFor(id))
          ?? PAGES.find((p) => p.id === id);
        if (target) navigate(target.route);
      },
      onOpen: (href: string) => {
        if (/^https?:/.test(href)) window.open(href, "_blank", "noopener");
        else navigate(href);
      },
      // Global search lives in the frame, so every page gets the same one.
      onSearch: (q: string) => globalSearch(q),
      searchScopes: SEARCH_SCOPES,
    }), [user, logout, navigate]);
    return (
      <Page
        data={data}
        loading={loading}
        error={error}
        actions={actions}
        chrome={chrome}
        mobile={useIsMobile()}
      />
    );
  };
}

const ROUTES = PAGES.map((page) => ({ page, Element: routeFor(page) }));

/** Session gate. While `/auth/me` is in flight nothing renders — showing the
 *  login screen for a beat and then swapping it for the console is worse than
 *  a blank frame. */
function Gate({ children }: { children: React.ReactNode }) {
  const { user, needsSetup, loading } = useAuth();
  if (loading) return null;
  if (needsSetup) return <Navigate to="/setup" replace />;
  if (!user) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

/** The mirror of Gate, and just as necessary: an authenticated visitor has no
 *  business on the sign-in or first-run screens. Without this, signing in
 *  succeeds — the cookie is set and /auth/me returns the user — and the login
 *  form just sits there, which is indistinguishable from the submit failing.
 *  Welcome is deliberately NOT here: onboarding is for a signed-in user. */
const AUTH_ONLY = new Set(["login", "setup"]);

function PublicOnly({ id, children }: { id: string; children: React.ReactNode }) {
  const { user, needsSetup, loading } = useAuth();
  if (loading) return null;
  if (AUTH_ONLY.has(id) && user) return <Navigate to="/" replace />;
  // First-run setup is finished: nobody should be able to re-run it.
  if (id === "setup" && !needsSetup && !user) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

/** Gives the library's <Link> the app's router.

    Without this every <a href> in the library falls back to a full page load:
    the SPA tears down and re-mounts, the session is re-fetched and any local
    state is lost — a "link" that behaves like typing the URL again. */
function Routed() {
  const navigate = useNavigate();
  return (
    <NavProvider navigate={navigate}>
      <Routes>
          {ROUTES.map(({ page, Element }) => (
            <Route
              key={page.id}
              path={page.route}
              element={PUBLIC.has(page.id)
                ? <PublicOnly id={page.id}><Element /></PublicOnly>
                : <Gate><Element /></Gate>}
            />
          ))}
          {/* Unknown path: back to the dashboard rather than a bespoke 404 —
              every real destination is in the registry above. */}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </NavProvider>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routed />
      </BrowserRouter>
    </AuthProvider>
  );
}
