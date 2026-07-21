import { useEffect, useRef, useState } from "react";
import { Navigate, NavLink, Outlet, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import * as Ic from "./components/icons";
import { Logo } from "./components/Logo";
import ChatDock, { ChatDockToggle, toggleDock } from "./components/chat/ChatDock";
import { MenuItem, Popover } from "./components/Popover";
import { SourceIcon } from "./components/shared";
import { IconRing, Tabs, Toaster, fmtAgo } from "./components/ui";
import { gql, useQuery } from "./lib/api";
import { useSettings } from "./pages/settings/shared";
import { AuthProvider, useAuth } from "./lib/auth";
import { BrandingProvider, useBranding } from "./lib/branding";
import "./pages/auth.css";
import Overview from "./pages/Overview";
import TasksPage from "./pages/Tasks";
import Knowledge from "./pages/Knowledge";
import DocReview from "./pages/DocReview";
import LineagePage from "./pages/Lineage";
import ClaimsPage from "./pages/Claims";
import FactsPage from "./pages/Facts";
import FlowsPage from "./pages/Flows";
import PublishPage from "./pages/Publish";
import SourcesPage from "./pages/Sources";
import LocalizationPage from "./pages/Localization";
import GeneralPage from "./pages/settings/General";
import MembersPage from "./pages/settings/Members";
import ModelsPage from "./pages/settings/Models";
import ApiKeysPage from "./pages/settings/ApiKeys";
import AuditLogPage from "./pages/settings/AuditLog";
import LibraryPage from "./pages/Library";
import WelcomePage from "./pages/Welcome";
import AnswersPage from "./pages/Answers";
import DecisionsPage from "./pages/Decisions";
import ReportsPage from "./pages/Reports";
import AuditPage from "./pages/Audit";
import LoginPage from "./pages/Login";
import LookbookPage from "./pages/Lookbook";
import SetupPage from "./pages/Setup";

const NAV_TOP = { to: "/", label: "Overview", icon: Ic.Home };

// Lifecycle-based grouping. Renames here are nav-label-only: routes/files/
// on-page headers stay put (e.g. "Sources" still opens /knowledge, whose own
// header still says "Search the knowledge base") — that was the deliberate
// low-risk call over a full route+file rename.
const NAV_GROUPS: { label: string; items: { to: string; label: string; icon: typeof Ic.Home }[] }[] = [
  {
    label: "Content",
    items: [
      { to: "/knowledge", label: "Sources", icon: Ic.Book },
      { to: "/claims", label: "Claims", icon: Ic.CheckCircle },
      { to: "/lineage", label: "Lineage", icon: Ic.LineageIcon },
    ],
  },
  {
    label: "Governance",
    items: [
      { to: "/library", label: "Policies", icon: Ic.ShieldCheck },
      { to: "/flows", label: "Workflows", icon: Ic.Flow },
    ],
  },
  {
    label: "Delivery & Insights",
    items: [
      { to: "/localization", label: "Localization", icon: Ic.Globe },
      { to: "/publish", label: "Publish", icon: Ic.Send },
      { to: "/reports", label: "Insights", icon: Ic.Chart },
    ],
  },
];

const SETTINGS_NAV = [
  { to: "/settings/general", label: "General" },
  { to: "/settings/members", label: "Members" },
  { to: "/settings/models", label: "Models" },
  { to: "/settings/sources", label: "Sources" },
  { to: "/settings/api-keys", label: "API keys" },
  { to: "/settings/audit", label: "Audit log" },
  { to: "/lookbook", label: "Design & brand" },
];


/** Workspace settings share one tab strip (layout route) instead of a
 *  sidebar sub-menu — every /settings/* page and /lookbook render below it. */
function SettingsLayout() {
  const location = useLocation();
  const navigate = useNavigate();
  const current = SETTINGS_NAV.find((s) => location.pathname.startsWith(s.to))?.to ?? SETTINGS_NAV[0].to;
  return (
    <>
      <div className="settings-tabs">
        <Tabs
          ariaLabel="Workspace settings"
          variant="underline"
          value={current}
          options={SETTINGS_NAV.map((s) => ({ id: s.to, label: s.label }))}
          onChange={(to) => navigate(to)}
        />
      </div>
      <Outlet />
    </>
  );
}

function Sidebar({ railed, onToggleRail }: { railed: boolean; onToggleRail: () => void }) {
  const location = useLocation();
  const inSettings = location.pathname.startsWith("/settings") || location.pathname === "/lookbook";
  const { branding } = useBranding();
  return (
    <aside className="sidebar">
      <button className="sidebar__collapse" onClick={onToggleRail} aria-label={railed ? "Expand sidebar" : "Collapse sidebar"}>
        {railed ? <Ic.ChevR size={14} /> : <Ic.ChevL size={14} />}
      </button>
      <div className="sidebar__brand">
        {branding.logo ? (
          <img className="sidebar__logo" src={branding.logo} alt={branding.logoAlt || "Workspace logo"} />
        ) : (
          <Logo />
        )}
        <span style={{ fontFamily: "var(--display)", fontWeight: 800, fontSize: 15, letterSpacing: "-0.02em", color: "var(--ink)" }}>mari</span>
      </div>

      <hr className="sidebar__rule" />

      <nav className="nav">
        <NavLink to={NAV_TOP.to} end className={({ isActive }) => `nav__item${isActive ? " active" : ""}`} title={NAV_TOP.label}>
          <NAV_TOP.icon size={17} />
          <span className="nav__label">{NAV_TOP.label}</span>
        </NavLink>

        {NAV_GROUPS.map((group) => (
          <div className="nav__group" key={group.label}>
            <span className="nav__group-label">{group.label}</span>
            {group.items.map((n) => (
              <NavLink key={n.to} to={n.to} className={({ isActive }) => `nav__item${isActive ? " active" : ""}`} title={n.label}>
                <n.icon size={17} />
                <span className="nav__label">{n.label}</span>
              </NavLink>
            ))}
          </div>
        ))}

        <hr className="sidebar__rule" style={{ margin: "10px 2px" }} />

        <NavLink to="/settings/general" className={() => `nav__item${inSettings ? " active" : ""}`} title="Settings">
          <Ic.Gear size={17} />
          <span className="nav__label">Settings</span>
        </NavLink>
      </nav>

      <button className="sidebar__help" onClick={() => toggleDock(true)}>
        <Ic.Chat size={16} />
        <span>
          <b>Need help?</b>
          <span>Ask Mari anything ↗</span>
        </span>
      </button>
    </aside>
  );
}

type Noti = { id: number; kind: string; text: string; detail: string; at: string; read: boolean };
type Hit = { source: string; title: string; snippet: string };

const NOTI_ICON: Record<string, React.ReactNode> = {
  factcheck: <Ic.ShieldCheck size={16} />,
  approval: <Ic.CheckCircle size={16} />,
  stale: <Ic.Clock size={16} />,
  mention: <Ic.Chat size={16} />,
  info: <Ic.Sparkle size={16} />,
};

/** Workspace chip — compact topbar chip showing the current workspace,
 *  links to workspace settings. (Single-workspace product; no switcher.) */
function ProjectSwitcher() {
  const navigate = useNavigate();
  const settings = useSettings();
  const name = settings.workspace?.name || "Workspace";
  return (
    <button
      className="topbar__project"
      title="Workspace settings"
      onClick={() => navigate("/settings/general")}
    >
      <Ic.Layers size={16} />
      <span>{name}</span>
    </button>
  );
}

function Bell() {
  const [open, setOpen] = useState(false);
  const notiQ = useQuery<Noti[]>(`{ notifications { id kind text detail at read } }`, { map: (d) => d.notifications ?? [] });
  // Render nothing until the API answers — no canned notifications, ever.
  const items = notiQ.data ?? [];
  const unread = items.filter((n) => !n.read).length;
  const markAll = async () => {
    await gql(`mutation { markNotificationsRead }`);
    notiQ.refetch();
  };
  return (
    <Popover
      open={open}
      onDismiss={() => setOpen(false)}
      panelClassName="drop"
      role="dialog"
      ariaLabel="Notifications"
      trigger={(
        <button className="iconbtn" onClick={() => setOpen((o) => !o)} aria-label="Notifications" aria-haspopup="dialog" aria-expanded={open}>
          <Ic.Bell size={17} />
          {unread > 0 && <span className="badge">{unread}</span>}
        </button>
      )}
    >
          <div className="drop__head">
            Notifications
            {unread > 0 && <button onClick={markAll}>Mark all read</button>}
          </div>
          {items.map((n) => (
            <div className={`noti${n.read ? "" : " unread"}`} key={n.id}>
              <IconRing size={28}>{NOTI_ICON[n.kind] ?? NOTI_ICON.info}</IconRing>
              <span className="noti__txt">
                {n.text}
                <span className="noti__sub">{n.detail}</span>
              </span>
              <span className="noti__at">{n.at}</span>
            </div>
          ))}
          {notiQ.data && items.length === 0 && <div className="noti"><span className="noti__txt">You're all caught up.</span></div>}
    </Popover>
  );
}

function CommandK({ onClose }: { onClose: () => void }) {
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<Hit[]>([]);
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => inputRef.current?.focus(), []);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- drop stale hits when the query empties, inside the debounced-search effect
    if (!query.trim()) { setHits([]); return; }
    const t = setTimeout(() => {
      gql(`query($q: String!) { search(query: $q, k: 6) { source title snippet } }`, { q: query })
        .then((d: any) => d?.search && setHits(d.search));
    }, 180);
    return () => clearTimeout(t);
  }, [query]);
  const go = () => { navigate(`/knowledge?q=${encodeURIComponent(query)}`); onClose(); };
  return (
    <div className="cmdk" onClick={onClose}>
      <div className="cmdk__panel" onClick={(e) => e.stopPropagation()}>
        <div className="cmdk__input">
          <Ic.Search size={18} />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && query.trim()) go(); if (e.key === "Escape") onClose(); }}
            placeholder="Search knowledge, people, facts…"
          />
          <kbd>esc</kbd>
        </div>
        {hits.map((h, i) => (
          <button className="cmdk__hit" key={i} onClick={go}>
            <SourceIcon source={h.source as any} size={20} />
            <span style={{ minWidth: 0 }}>
              <h4>{h.title}</h4>
              <p>{h.snippet.slice(0, 90)}</p>
            </span>
          </button>
        ))}
        <div className="cmdk__foot">
          <span><b>↵</b> open in Knowledge</span>
          <span><b>esc</b> close</span>
          <span style={{ marginLeft: "auto" }}>Hybrid search · BM25 + embeddings</span>
        </div>
      </div>
    </div>
  );
}

/** Last successful sync, from real sync events — renders nothing until the
 *  API reports one (no canned "Synced 2m ago", ever). */
function SyncedBadge() {
  const eventsQ = useQuery<{ event: string; at: string }[]>(
    `{ syncEvents { event at } }`,
    { map: (d) => d.syncEvents ?? [] },
  );
  // events arrive newest-first; successful syncs look like "sync: repo @ abc",
  // "upload: 3 file(s)", "repo ingest: …" — failures ("sync failed: …") don't match.
  const last = (eventsQ.data ?? []).find((e) => /^(sync|upload|repo ingest):/.test(e.event));
  const ago = last ? fmtAgo(last.at) : "";
  if (!ago) return null;
  return (
    <div className="sync">
      <span className="sync__dot" />
      <span>
        <b>Synced</b>
        {ago}
      </span>
    </div>
  );
}

function Topbar() {
  const [cmdk, setCmdk] = useState(false);
  const [userMenu, setUserMenu] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const { user, logout } = useAuth();
  // Real session identity only — never seed data. (The shell only renders
  // signed-in, so the generic fallback is a belt-and-braces placeholder.)
  const displayName = user?.name || "Account";
  const displayInitials = user?.initials || "?";
  const avatarClass = `avatar${user?.tint ? ` avatar--tint${user.tint}` : ""}`;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") { e.preventDefault(); setCmdk((s) => !s); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  // eslint-disable-next-line react-hooks/set-state-in-effect -- close the user menu on navigation; one boolean reset, no cascade
  useEffect(() => setUserMenu(false), [location.pathname]);
  return (
    <header className="topbar">
      <button className="topbar__search" role="search" onClick={() => setCmdk(true)}>
        <Ic.Search size={18} />
        Search knowledge, people, facts…
        <span className="card__spacer" />
        <kbd>⌘K</kbd>
      </button>
      <div className="topbar__right">
        <ProjectSwitcher />
        <div className="topbar__divider" />
        <SyncedBadge />
        <ChatDockToggle />
        <Bell />
        <div className="topbar__divider" />
        <Popover
          open={userMenu}
          onDismiss={() => setUserMenu(false)}
          trigger={(
            <button className="user" aria-haspopup="menu" aria-expanded={userMenu} onClick={() => setUserMenu((s) => !s)}>
              <span className={avatarClass}>{displayInitials}</span>
              <span>{displayName}</span>
              <span className="chev"><Ic.Chev size={16} /></span>
            </button>
          )}
        >
          <MenuItem icon={<Ic.Gear size={15} />} onClick={() => { navigate("/settings/general"); setUserMenu(false); }}>Preferences</MenuItem>
          <MenuItem icon={<Ic.Sprout size={15} />} onClick={() => { navigate("/welcome"); setUserMenu(false); }}>Setup guide</MenuItem>
          <MenuItem icon={<Ic.Key size={15} />} onClick={() => { navigate("/settings/api-keys"); setUserMenu(false); }}>API keys</MenuItem>
          <MenuItem icon={<Ic.Clock size={15} />} onClick={() => { navigate("/settings/audit"); setUserMenu(false); }}>Audit log</MenuItem>
          <MenuItem icon={<Ic.ArrowR size={15} />} onClick={() => { setUserMenu(false); void logout(); }}>Sign out</MenuItem>
        </Popover>
      </div>
      {cmdk && <CommandK onClose={() => setCmdk(false)} />}
    </header>
  );
}

function Splash() {
  return (
    <div className="auth-splash" aria-label="Loading Mari Cloud">
      <Logo size={48} />
    </div>
  );
}

/** Auth gate: splash while loading; /setup on first run; /login when signed
 *  out; the full app shell once signed in. */
function Gate() {
  const { user, needsSetup, loading } = useAuth();
  if (loading) return <Splash />;
  if (needsSetup) {
    return (
      <Routes>
        <Route path="/setup" element={<SetupPage />} />
        <Route path="*" element={<Navigate to="/setup" replace />} />
      </Routes>
    );
  }
  if (!user) {
    return (
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/setup" element={<SetupPage />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    );
  }
  return <AppShell />;
}

export default function App() {
  return (
    <AuthProvider>
      <Gate />
    </AuthProvider>
  );
}

function AppShell() {
  const [railed, setRailed] = useState(() => localStorage.getItem("mari.rail") === "1");
  const toggleRail = () => setRailed((r) => { localStorage.setItem("mari.rail", r ? "0" : "1"); return !r; });
  return (
    <BrandingProvider>
    <Toaster>
    <div className={`app${railed ? " app--railed" : ""}`}>
      <Sidebar railed={railed} onToggleRail={toggleRail} />
      <div className="main">
        <Topbar />
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/tasks" element={<TasksPage />} />
          {/* Ask Mari is no longer a screen — the agent dock is everywhere. */}
          <Route path="/ask" element={<Navigate to="/" replace />} />
          <Route path="/knowledge" element={<Knowledge />} />
          <Route path="/knowledge/doc" element={<DocReview />} />
          <Route path="/library" element={<LibraryPage />} />
          <Route path="/answers" element={<AnswersPage />} />
          <Route path="/decisions" element={<DecisionsPage />} />
          <Route path="/claims" element={<ClaimsPage />} />
          <Route path="/localization" element={<LocalizationPage />} />
          <Route path="/reports" element={<ReportsPage />} />
          <Route path="/welcome" element={<WelcomePage />} />
          <Route element={<SettingsLayout />}>
            <Route path="/settings/general" element={<GeneralPage />} />
            {/* Branding merged into the Design lookbook — old links land there. */}
            <Route path="/settings/branding" element={<Navigate to="/lookbook" replace />} />
            <Route path="/settings/members" element={<MembersPage />} />
            <Route path="/settings/models" element={<ModelsPage />} />
            <Route path="/settings/sources" element={<SourcesPage />} />
            <Route path="/settings/api-keys" element={<ApiKeysPage />} />
            <Route path="/settings/audit" element={<AuditLogPage />} />
          </Route>
          <Route path="/lineage" element={<LineagePage />} />
          <Route path="/facts" element={<FactsPage />} />
          <Route path="/flows" element={<FlowsPage />} />
          <Route path="/publish" element={<PublishPage />} />
          {/* /audit is intentionally absent from the sidebar nav: the knowledge
              audit is reachable from Sources only (DESIGN.md §21.4-style
              "entry from the owning page" convention), not a top-level area. */}
          <Route path="/audit" element={<AuditPage />} />
          <Route element={<SettingsLayout />}>
            <Route path="/lookbook" element={<LookbookPage />} />
          </Route>
          <Route path="/login" element={<Navigate to="/" replace />} />
          <Route path="/setup" element={<Navigate to="/" replace />} />
        </Routes>
      </div>
      <ChatDock />
    </div>
    </Toaster>
    </BrandingProvider>
  );
}
