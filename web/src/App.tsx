import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { PAGES, type PageModule } from "@mari-design/components/pages";
import { adapterFor } from "./data";
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
function routeFor(page: PageModule<any>) {
  const Page = page.component;
  const useData = adapterFor(page.id);

  return function PageRoute() {
    const { data, loading, error } = useData();
    return <Page data={data} loading={loading} error={error} mobile={useIsMobile()} />;
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

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          {ROUTES.map(({ page, Element }) => (
            <Route
              key={page.id}
              path={page.route}
              element={PUBLIC.has(page.id) ? <Element /> : <Gate><Element /></Gate>}
            />
          ))}
          {/* Unknown path: back to the dashboard rather than a bespoke 404 —
              every real destination is in the registry above. */}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
