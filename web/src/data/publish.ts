/* Publish adapter — the doc site being edited and the workspace's MCP servers.
 *
 * `releases` takes an optional site id (added for this page), so the site, its
 * release history and the MCP list all arrive in one document instead of
 * chaining a second round trip on the first site's id. */

import type {
  DocSite, McpCreated, McpDraft, NavSection, PublishData, PublishGate,
  SiteFeature, SiteRelease, SiteTheme,
} from "@mari-design/components/pages/PublishPage";
import type { McpServer } from "@mari-design/components/features/PublishMcpServers";
import { useQuery } from "../lib/api";
import type { PageData } from "./types";

/* ── query ──────────────────────────────────────────────────────────────── */

const QUERY = `{
  sites { id name domain status theme sources nav gates docs warnings }
  releases { id siteId version status deployed docs notes }
  mcpServers { id name url scope status tools config }
  siteThemePresets { key name accent bg }
  settings { key value }
}`;

/* The generator's switches resolve per site: the shipped default overlaid with
   whatever that site stored. Which site is "the" site only falls out of the
   first response, so this is a second, dependent round trip rather than a
   guess — asking for the defaults and calling them the site's settings would
   show a toggle in a position the built site does not honour. */
const FEATURES_QUERY = `query($siteId: Int) {
  siteFeatures(siteId: $siteId) { key label hint on }
}`;

type Res = {
  sites: {
    id: number; name: string; domain: string; status: string;
    theme: Record<string, unknown> | null;
    sources: unknown;
    nav: { label?: string; docs?: number }[] | null;
    gates: { gate?: string; status?: string }[] | null;
    docs: number; warnings: number;
  }[];
  releases: { id: number; siteId: number; version: string; status: string; deployed: string; docs: number; notes: string }[];
  mcpServers: {
    id: number; name: string; url: string; scope: string; status: string;
    tools: number; config: { capabilities?: string[] } | null;
  }[];
  siteThemePresets: { key: string; name: string; accent: string; bg: string }[];
  settings: { key: string; value: unknown }[];
};

type FeaturesRes = { siteFeatures: { key: string; label: string; hint: string; on: boolean }[] };

/* ── mapping helpers ────────────────────────────────────────────────────── */

/* The MCP list draws two states and five scopes. A row outside either would
   render an unlabelled chip, so it is dropped — the server and this build of
   the library ship separately. */
const MCP_STATUS = new Set<McpServer["status"]>(["connected", "idle"]);
const MCP_SCOPE = new Set<McpServer["scope"]>(["workspace", "product", "team", "org", "public", "project"]);

export function mapServers(res: Res): McpServer[] {
  return (res.mcpServers ?? [])
    .filter((s) => MCP_STATUS.has(s.status as McpServer["status"]) && MCP_SCOPE.has(s.scope as McpServer["scope"]))
    .map<McpServer>((s) => ({
      id: s.id,
      name: s.name,
      url: s.url,
      scope: s.scope as McpServer["scope"],
      status: s.status as McpServer["status"],
      capabilities: s.config?.capabilities ?? [],
    }));
}

/** The workspace's one doc site: the live one if there is one, else the first.
 *  A workspace with no site returns null, which is what makes the page's own
 *  "publishes nothing" state true. */
/** The workspace's one doc site, before its release history is attached: the
 *  live one if there is one, else the first. `siteFeatures` is asked for by
 *  this id, so both halves of the page describe the same site. */
export function pickSiteRow(res: Res): Res["sites"][number] | null {
  const sites = res.sites ?? [];
  return sites.find((s) => s.status === "live") ?? sites[0] ?? null;
}

export function mapSite(res: Res, features: SiteFeature[]): DocSite | null {
  const site = pickSiteRow(res);
  if (!site) return null;

  const releases = (res.releases ?? []).filter((r) => r.siteId === site.id);
  const live = releases.find((r) => r.status === "live") ?? releases[0];
  const theme = site.theme ?? {};
  const accent = typeof theme.accent === "string" ? theme.accent : "";
  const deploy = (res.settings ?? []).find((s) => s.key === "deploy");
  const deployCfg = (deploy && typeof deploy.value === "object" && deploy.value !== null
    ? deploy.value : {}) as { bucket?: string; region?: string };
  const nav = (site.nav ?? [])
    .filter((n) => n.label)
    .map<NavSection>((n) => ({ label: n.label!, docs: n.docs ?? 0 }));

  return {
    name: site.name,
    domain: site.domain,
    // The version the next deploy builds on: the newest release there is.
    version: live?.version ?? "",
    // sites.sources is the tag list that decides which documents are eligible.
    sourceTags: Array.isArray(site.sources) ? site.sources.filter((t): t is string => typeof t === "string") : [],
    docsMatched: site.docs,
    warnings: site.warnings ? `${site.warnings} warning${site.warnings === 1 ? "" : "s"}` : "",
    nav,
    previewNav: nav.map((n) => n.label),
    // Stored as [{gate, status}] by create_site and rewritten by each build.
    gates: (site.gates ?? [])
      .filter((g) => g.gate)
      .map<PublishGate>((g) => ({ name: g.gate!, ok: g.status === "pass", note: g.status ?? "" })),
    // The one accent this site actually uses. There is no palette to choose
    // from in the theme row, so offering five would be inventing four.
    accents: accent ? [accent] : [],
    // The themes the generator can actually render, with the accent each one
    // ships. sitebuilder reads the same rows, so nothing here is offered that
    // a build would ignore.
    themes: (res.siteThemePresets ?? []).map<SiteTheme>((t) => ({
      key: t.key, name: t.name, accent: t.accent, bg: t.bg,
    })),
    // Resolved for this site: the shipped default overlaid with the site's
    // stored override.
    features,
    bucket: deployCfg.bucket ?? "",
    region: deployCfg.region ?? "",
    releases: releases.map<SiteRelease>((r) => ({ version: r.version, note: r.notes })),
    // What deploy_site recorded about where the live release went.
    releasedNote: live?.notes ?? "",
  };
}

/** The new-server form and the one-time token screen. Both belong to the
 *  create flow — the draft is a form nobody has opened, and the token exists
 *  only in `createMcpServer`'s response, once. */
const NO_DRAFT: McpDraft = { name: "", scope: "workspace", capabilities: [], toolCount: 0 };
const NO_CREATED: McpCreated = { name: "", scopeLabel: "", toolCount: 0, token: "", snippet: "" };

/* ── mapper ─────────────────────────────────────────────────────────────── */

export const EMPTY: PublishData = {
  view: "site-editor", editorTab: "content", phase: "draft",
  site: null, servers: [], serverCount: 0, draft: NO_DRAFT, created: NO_CREATED,
};

/** Pure: the whole response → everything Publish renders. `features` comes
 *  from the dependent `siteFeatures(siteId:)` query — [] until it answers,
 *  which renders the switch list as the empty block it is at that moment. */
export function buildPublish(res: Res | null, features: SiteFeature[] = []): PublishData {
  if (!res) return EMPTY;
  const servers = mapServers(res);
  const site = mapSite(res, features);
  const row = pickSiteRow(res);
  return {
    // `/publish` opens on the site editor's content tab. The deploy flow and
    // the three MCP screens are routes this app does not have yet.
    view: "site-editor",
    editorTab: "content",
    // A deploy is in flight only while a mutation is running; a page load is
    // looking at what is already there.
    phase: row?.status === "live" ? "published" : "draft",
    site,
    servers,
    serverCount: servers.length,
    draft: NO_DRAFT,
    created: NO_CREATED,
  };
}

/* ── adapter ────────────────────────────────────────────────────────────── */

export function usePublish(): PageData<PublishData> {
  const q = useQuery<Res>(QUERY, { map: (d: Res) => d });
  // Which site the switches belong to is only known once the first query has
  // answered. `siteId: null` asks for the shipped defaults, which is what the
  // page needs when there is no site at all — and in that case `site` is null,
  // so nothing renders them.
  const siteId = q.data ? pickSiteRow(q.data)?.id ?? null : null;
  const f = useQuery<SiteFeature[]>(FEATURES_QUERY, {
    variables: { siteId },
    map: (d: FeaturesRes) => d.siteFeatures ?? [],
  });
  return {
    data: buildPublish(q.data, f.data ?? []),
    loading: q.loading,
    error: q.error ? (q.errorText ?? "Publishing is temporarily unavailable.") : null,
  };
}
