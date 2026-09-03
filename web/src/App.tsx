import { useEffect, useMemo, useRef, type ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { PAGES, type PageModule } from "@mari-design/components/pages";
import { landingPageFor, type ShellChrome } from "@mari-design/components/pages/PageFrame";
import { NavProvider } from "@mari-design/components/navigation/Link";
import { adapterFor } from "./data";
import { STUBBED } from "./data/stubs";
import { useChrome } from "./data/chrome";
import { SEARCH_SCOPES, globalSearch } from "./data/search";
import { ACTION_FACTORIES } from "./data/actions";
import { AuthProvider, useAuth } from "./lib/auth";
import { AgentDock, AgentDockProvider } from "./components/AgentDock";
import { KnowledgeChatDestination } from "./components/KnowledgeChatDestination";
import { FactScanConfiguration } from "./components/FactScanConfiguration";
import { useIsMobile } from "./lib/mobile";

/* The whole console, routed off the component library's own page registry.
 *
 * There is no page code in this app. `PAGES` supplies the route, the title and
 * the component; `src/data/` supplies the data. Adding a page to the library
 * adds it here — the only local step is writing its adapter. */

/** Ids that must stay reachable without a session.
 *
 *  Welcome was here and is not: onboarding is for a signed-in user (see
 *  AUTH_ONLY below, which already said so), and its adapter reads the
 *  connector catalog, the workspace's repos and its sync rows. Reachable
 *  without a session, that query could only come back 401, and the page
 *  rendered the read-error card at a visitor whose actual problem was that
 *  they were not signed in. */
const PUBLIC = new Set(["login", "setup"]);

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
  const { user, logout, projects, activeProject, selectProject } = useAuth();
  const navigate = useNavigate();
  const { pathname } = useLocation();
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
    projects: projects.map((project) => ({ id: project.id, name: project.name, detail: project.role })),
    activeProjectId: activeProject?.id,
    onSelectProject: (project) => {
      const selected = projects.find((candidate) => String(candidate.id) === String(project.id));
      if (selected) void selectProject(selected);
    },
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
    // The agent rides beside every routed page and survives route changes:
    // its transcript lives in AgentDockProvider, so the surface can remount
    // with each page's frame. The published knowledge chat is its own
    // conversation and gets no second one floating over it.
    aside: pathname.startsWith("/knowledge-chat/") ? undefined : <AgentDock />,
  }), [user, logout, projects, activeProject, selectProject, navigate, notifications, recentSearches, pathname]);
}


/** Builds one route component per page, at module load, so the adapter hook is
 *  a fixed call for that component and never varies between renders. */
function routeFor(page: PageModule<any, any>) {
  const Page = page.component;
  const useData = adapterFor(page.id);
  const makeActions = ACTION_FACTORIES[page.id];

  return function PageRoute() {
    const { data, loading, error } = useData();
    const { refresh, user } = useAuth();
    const navigate = useNavigate();
    const chrome = useShellChrome();
    // Rebuilt only when `refresh` changes, so a page never sees a new actions
    // object on every render (which would defeat memoised children).
    const actions = useMemo(
      () => makeActions?.({
        refresh,
        currentUserName: user?.name ?? "",
        navigate: (href: string) => navigate(href),
        replace: (href: string) => navigate(href, { replace: true }),
      }),
      [refresh, user?.name, navigate],
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

/** /trajectories and /answers -> the Workflows tab that replaced them.
 *
 *  A bare `<Navigate to="/workflows">` would drop the query string, and the
 *  query string is the whole reason these routes still exist: the links people
 *  hold are `/answers?answer=4` (a Review item, a chat citation) and
 *  `/trajectories?category=…` (a bookmark). Everything the old route carried
 *  is carried through, with `tab` set to the half that used to be the page. */
/** The retired ?tab=scheduled half of Workflows is /scheduled-tasks now.
 *
 *  Old notifications and bookmarks still carry the query. Without this the
 *  unknown tab falls back to Observed, which reads like the schedule vanished
 *  rather than moved. */
function LegacyScheduledTab({ children }: { children: ReactNode }) {
  const { search } = useLocation();
  if (new URLSearchParams(search).get("tab") === "scheduled") {
    return <Navigate to="/scheduled-tasks" replace />;
  }
  return <>{children}</>;
}

function MovedToWorkflows({ tab }: { tab: "observed" | "answers" }) {
  const { search } = useLocation();
  const params = new URLSearchParams(search);
  if (tab === "answers") params.set("tab", "answers");
  else params.delete("tab");
  const query = params.toString();
  return <Navigate to={query ? `/workflows?${query}` : "/workflows"} replace />;
}

/** Gives the library's <Link> the app's router.

    Without this every <a href> in the library falls back to a full page load:
    the SPA tears down and re-mounts, the session is re-fetched and any local
    state is lost — a "link" that behaves like typing the URL again. */
function Routed() {
  const navigate = useNavigate();
  return (
    <NavProvider navigate={navigate}>
      <AgentDockProvider>
      <RouteFocus />
      <Routes>
          <Route path="/knowledge-chat/:project/:slug" element={<KnowledgeChatDestination />} />
          <Route path="/trajectories" element={<Navigate to="/workflows" replace />} />
          {ROUTES.map(({ page, Element }) => (
            <Route
              key={page.id}
              path={page.route}
              element={PUBLIC.has(page.id)
                ? <PublicOnly id={page.id}><Element /></PublicOnly>
                : page.id === "workflows"
                  ? <Gate><LegacyScheduledTab><Element /></LegacyScheduledTab></Gate>
                  : <Gate><Element /></Gate>}
            />
          ))}
        {/* Retired surfaces. The Flows pipeline editor and the Review page
            are gone, but their URLs are in bookmarks, in old notifications and
            in links people pasted to each other. Sending them to the surface
            that replaced the work is kinder than the catch-all below, which
            would drop someone on the dashboard with no idea why. */}
        <Route path="/flows" element={<Navigate to="/workflows" replace />} />
        <Route path="/tasks" element={<Navigate to="/" replace />} />
        {/* Trajectories and Answers are the two tabs of Workflows now. These
            keep their query strings, because the queries are what make the old
            links worth honouring: /answers?answer=4 is what a Review item and
            a served chat citation point at, and /trajectories?category=… is
            what somebody bookmarked. */}
        <Route path="/trajectories" element={<MovedToWorkflows tab="observed" />} />
        <Route path="/answers" element={<MovedToWorkflows tab="answers" />} />
        {/* Unknown path: say so. Behind the session gate, because a signed-out
            visitor's problem is that they are signed out, not that the page is
            missing. */}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      <FactScanConfiguration />
      </AgentDockProvider>
    </NavProvider>
  );
}

/** Move keyboard/screen-reader context after an SPA page navigation. Skip the
 * first render so a direct load keeps the browser's normal starting point.
 * preventScroll: on iOS Safari the shell is taller than the visible viewport
 * while the toolbars are showing, and a scrolling focus on <main> pushed the
 * header (and its menu button) off the top of the screen on every
 * navigation, the sign-in redirect included. */
function RouteFocus() {
  const { pathname } = useLocation();
  const first = useRef(true);
  useEffect(() => {
    if (first.current) { first.current = false; return; }
    const frame = requestAnimationFrame(() => document.getElementById("main-content")?.focus({ preventScroll: true }));
    return () => cancelAnimationFrame(frame);
  }, [pathname]);
  return null;
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
