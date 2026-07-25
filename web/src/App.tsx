import { useMemo } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { PAGES, type PageModule } from "@mari-design/components/pages";
import { PageFrame, landingPageFor, navFor, type ShellChrome } from "@mari-design/components/pages/PageFrame";
import { NavProvider } from "@mari-design/components/navigation/Link";
import { Button, Card, EmptyState, PageHeader } from "@mari-design/components";
import { adapterFor } from "./data";
import { STUBBED } from "./data/stubs";
import { useChrome } from "./data/chrome";
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

/** Pages the library ships for itself, not for a workspace. The Lookbook is
 *  the design system exhibiting itself — its content is a set of deliberately
 *  pathological strings for truncation testing, and it is the one page with no
 *  adapter (`stubs.ts` hands it `null`). Routing it here put the library's own
 *  catalog inside a customer's console, at a URL anybody could reach. */
const LIBRARY_ONLY = new Set<string>(STUBBED);

/** Everything the console routes: the library's registry minus its own
 *  exhibits. A page added to the library appears here the moment it has an
 *  adapter — the only local step. */
const APP_PAGES = PAGES.filter((p) => !LIBRARY_ONLY.has(p.id));

/** The chrome the frame draws around every page — session, navigation, global
 *  search, notifications. One hook, so the not-found page below is framed by
 *  the same console as everything else rather than a bare white box. */
function useShellChrome(): ShellChrome {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  // The bell and the search overlay are the same on every page, so they are
  // fetched here rather than by 25 page adapters.
  const { notifications, recentSearches } = useChrome();
  // The real session drives the topbar. The frame names the signed-out state
  // itself when this is absent; it never invents a person.
  return useMemo(() => ({
    user: user
      ? { name: user.name, initials: user.initials, detail: user.role === "admin" ? "Admin" : user.role }
      : undefined,
    onSignOut: logout,
    // The sidebar emits a NAV id, which is not always a page id: five
    // Settings pages share the "settings" nav item, so looking the nav id up
    // directly found nothing and Settings went nowhere. The library resolves
    // a nav id to the page it should land on; the registry knows its route.
    onNavigate: (id: string) => {
      const target = APP_PAGES.find((p) => p.id === landingPageFor(id))
        ?? APP_PAGES.find((p) => p.id === id);
      if (target) navigate(target.route);
    },
    onOpen: (href: string) => {
      if (/^https?:/.test(href)) window.open(href, "_blank", "noopener");
      else navigate(href);
    },
    // Global search lives in the frame, so every page gets the same one.
    onSearch: (q: string) => globalSearch(q),
    searchScopes: SEARCH_SCOPES,
    // Real rows from the workspace's notification table, and the queries it
    // has actually run. Both were absent, which made the bell a zero on every
    // page and the search overlay's "recent" list permanently empty.
    notifications,
    recentSearches,
  }), [user, logout, navigate, notifications, recentSearches]);
}

/** A path this console does not route.
 *
 * It used to redirect to the dashboard with no message, which is
 * indistinguishable from the link having worked and the dashboard being what
 * was asked for. This says what happened, quotes the path so a bad link can be
 * reported, and offers the one destination that certainly exists. */
function NotFoundPage() {
  const chrome = useShellChrome();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  return (
    <PageFrame chrome={chrome} active={navFor("overview")} title="Page not found" mobile={useIsMobile()}>
      <div className="mx-auto max-w-[1400px] px-5 py-6 sm:px-8">
        <PageHeader eyebrow="404" title="Page not found" />
        <Card className="mt-6">
          <EmptyState
            title="Nothing is routed here"
            action={<Button onClick={() => navigate("/")}>Go to the dashboard</Button>}
          >
            The console has no page at {pathname}. The link may be out of date, or the
            page may have moved.
          </EmptyState>
        </Card>
      </div>
    </PageFrame>
  );
}

/** Builds one route component per page, at module load, so the adapter hook is
 *  a fixed call for that component and never varies between renders. */
function routeFor(page: PageModule<any, any>) {
  const Page = page.component;
  const useData = adapterFor(page.id);
  const makeActions = ACTION_FACTORIES[page.id];

  return function PageRoute() {
    const { data, loading, error } = useData();
    const { refresh } = useAuth();
    const navigate = useNavigate();
    const chrome = useShellChrome();
    // Rebuilt only when `refresh` changes, so a page never sees a new actions
    // object on every render (which would defeat memoised children).
    const actions = useMemo(
      () => makeActions?.({
        refresh,
        navigate: (href: string) => navigate(href),
        replace: (href: string) => navigate(href, { replace: true }),
      }),
      [refresh, navigate],
    );
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

const ROUTES = APP_PAGES.map((page) => ({ page, Element: routeFor(page) }));

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
        {/* Unknown path: say so. Behind the session gate, because a signed-out
            visitor's problem is that they are signed out, not that the page is
            missing. */}
        <Route path="*" element={<Gate><NotFoundPage /></Gate>} />
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
