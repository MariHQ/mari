/* Publish adapter — the doc site being edited and the workspace's MCP servers.
 *
 * `releases` takes an optional site id (added for this page), so the site, its
 * release history and the MCP list all arrive in one document instead of
 * chaining a second round trip on the first site's id.
 *
 * `/publish` is the list of doc sites — the workspace has as many as it has
 * made, and this is where the next one gets created. `/publish?site=<id>` is
 * that site's editor. `/publish?tab=mcp` is the MCP servers half.
 *
 * The tab is in the URL because the page reports it (`openSection`) the way
 * Knowledge reports its query: a Publish tab then survives a reload and can be
 * linked, instead of being state that dies with the render. */

import { useEffect, useMemo, useRef } from "react";
import { useSearchParams } from "react-router-dom";

import type {
  DocSite, McpCreated, McpDraft, NavSection, PublishData, PublishGate,
  PublishSection, SiteFeature, SiteRelease, SiteSummary, SiteTheme,
} from "@mari-design/components/pages/PublishPage";
import type { McpServer } from "@mari-design/components/features/PublishMcpServers";
import { useQuery } from "../lib/api";
import type { PageData } from "./types";
import { EMPTY_BOTS, mapBots, type BotsStatusResponse } from "./bots";

/* ── query ──────────────────────────────────────────────────────────────── */

const QUERY = `{
  sites { id name domain status theme sources nav gates docs warnings }
  releases { id siteId version status deployed docs notes }
  mcpServers { id name url scope status tools config }
  siteThemePresets { key name accent bg }
  settings { key value }
  tagDefs { tag }
  botsStatus
}`;

/* The generator's switches resolve per site: the shipped default overlaid with
   whatever that site stored. Which site is "the" site only falls out of the
   first response, so this is a second, dependent round trip rather than a
   guess — asking for the defaults and calling them the site's settings would
   show a toggle in a position the built site does not honour. */
const FEATURES_QUERY = `query($siteId: Int) {
  siteFeatures(siteId: $siteId) { key label hint on }
}`;

type Res = BotsStatusResponse & {
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
  tagDefs: { tag: string }[];
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

/** Every doc site, as the list shows them. */
export function mapSites(res: Res): SiteSummary[] {
  return (res.sites ?? []).map<SiteSummary>((s) => ({
    id: s.id,
    name: s.name,
    domain: s.domain,
    // The list draws two states; anything else has not been released, which is
    // what "draft" says.
    status: s.status === "live" ? "live" : "draft",
    docs: s.docs,
  }));
}

/** The site an editor is open on: the one `?site=` names. Null when the route
 *  names none (the list) or names one that is not there — `siteFeatures` is
 *  asked for by this same id, so both halves of the page describe one site. */
export function pickSiteRow(res: Res, siteId: number | null): Res["sites"][number] | null {
  if (siteId == null) return null;
  return (res.sites ?? []).find((s) => s.id === siteId) ?? null;
}

export function mapSite(res: Res, siteId: number | null, features: SiteFeature[]): DocSite | null {
  const site = pickSiteRow(res, siteId);
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
    id: site.id,
    name: site.name,
    domain: site.domain,
    // Two states, and only a deployed one is serving anything.
    status: site.status === "live" ? "live" : "draft",
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
  view: "site-list", editorTab: "content", phase: "draft",
  sites: [], tagOptions: [], site: null, servers: [], serverCount: 0,
  draft: NO_DRAFT, created: NO_CREATED,
  slack: EMPTY_BOTS.slack, github: EMPTY_BOTS.github,
};

/** Pure: the whole response → everything Publish renders. `features` comes
 *  from the dependent `siteFeatures(siteId:)` query — [] until it answers,
 *  which renders the switch list as the empty block it is at that moment. */
export function buildPublish(
  res: Res | null, siteId: number | null = null, creating = false, features: SiteFeature[] = [],
  section: PublishSection = "sites",
): PublishData {
  /* `?tab=mcp` is the MCP half whatever else the route says, so the tab the
     reader clicked is the tab a reload comes back to. */
  const opening = (site: DocSite | null): PublishData["view"] =>
    section === "bots" ? "bots" : section === "mcp" ? "mcp-list"
      : site ? "site-editor" : creating ? "site-new" : "site-list";
  if (!res) return { ...EMPTY, view: opening(null) };
  const servers = mapServers(res);
  const bots = mapBots(res);
  const site = mapSite(res, siteId, features);
  const row = pickSiteRow(res, siteId);
  return {
    // `/publish` is the site list, `?new` its create form, `?site=` one site's
    // editor, `?tab=mcp` the MCP server list. The deploy flow, the add-server
    // form and the token screen are routes this app does not have yet — the
    // MCP panel opens its own create form and shows its own token.
    view: opening(site),
    editorTab: "content",
    // A deploy is in flight only while a mutation is running; a page load is
    // looking at what is already there.
    phase: row?.status === "live" ? "published" : "draft",
    sites: mapSites(res),
    // What a new site can draw its documents from: the tags this workspace
    // actually defines, so the form cannot offer a filter that matches nothing.
    tagOptions: (res.tagDefs ?? []).map((t) => t.tag).filter(Boolean),
    site,
    servers,
    serverCount: servers.length,
    draft: NO_DRAFT,
    created: NO_CREATED,
    slack: bots.slack,
    github: bots.github,
  };
}

/* ── adapter ────────────────────────────────────────────────────────────── */

export function usePublish(): PageData<PublishData> {
  const [params] = useSearchParams();
  const asked = Number(params.get("site"));
  const askedId = Number.isInteger(asked) && asked > 0 ? asked : null;
  const creating = params.get("new") !== null;
  // Which top-level tab the reader is on. Anything but the MCP half is the
  // doc sites half, which is also what a URL with no `tab` at all means.
  const tab = params.get("tab");
  const section: PublishSection = tab === "mcp" || tab === "bots" ? tab : "sites";
  const q = useQuery<Res>(QUERY, { map: (d: Res) => d });

/* The route names the subject; the query behind it takes no variables, so
   navigating from the list to one site does not change useQuery's key and
   the page would render the new site off a response taken before it
   existed. Creating one lands on exactly that route, so the read is re-run
   whenever the subject changes. Mount is not a change: the ref starts on the
   subject the first render already fetched. */
  const { refetch } = q;
  const seen = useRef<number | null>(askedId);
  useEffect(() => {
    if (seen.current === askedId) return;
    seen.current = askedId;
    refetch();
  }, [askedId, refetch]);

  // The switches belong to the site the route names, and only once the first
  // query has confirmed that site exists. `siteId: null` asks for the shipped
  // defaults, which is what the page needs when no editor is open — and in
  // that case `site` is null, so nothing renders them.
  const siteId = q.data ? pickSiteRow(q.data, askedId)?.id ?? null : null;
  const f = useQuery<SiteFeature[]>(FEATURES_QUERY, {
    variables: { siteId },
    map: (d: FeaturesRes) => d.siteFeatures ?? [],
  });
  /* Built once per RESPONSE, not once per render. The site editor seeds the
     nav tree and the feature switches from `site` and resyncs on
     `seen !== site` (object identity), and the sites list re-adopts `sites`
     from a `[sites]` effect — so rebuilding here every render would reset the
     nav the user is reordering and the switches they are flipping, on every
     render. `f.data ?? []` is also a new empty array per render, which is the
     same trap on a site whose feature list is genuinely empty. */
  const data = useMemo(
    () => buildPublish(q.data, askedId, creating, f.data ?? [], section),
    [q.data, askedId, creating, f.data, section],
  );
  return {
    data,
    loading: q.loading,
    error: q.error ? (q.errorText ?? "Publishing is temporarily unavailable.") : null,
  };
}
