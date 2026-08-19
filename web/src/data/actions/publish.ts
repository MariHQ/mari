/* Publish actions — the doc site and the workspace's MCP servers.
 *
 * The page edits ONE site and does not carry its id (the adapter maps a row
 * onto a presentational shape), so the site handlers resolve it here from the
 * route — `?site=<id>` — which is the same thing the adapter reads. That keeps
 * the two halves talking about the same site without the page having to carry
 * a database key it never renders.
 *
 * Two intents stay unwired, and so undrawn:
 *
 *   • `deleteSite`. There is no mutation that removes a site — `createSite`,
 *     `updateSiteTheme`, `setSiteFeature`, `buildSite`, `deploySite` and
 *     `rollbackRelease` are the whole surface — so the list row offers no
 *     Delete rather than a confirm dialog that takes nothing away.
 *   • `setSiteNav`. `sites.nav` is written by the builder from the documents a
 *     build actually matched; nothing accepts a nav tree from the console, and
 *     a Save that reordered sections only on screen would be undone by the
 *     next build. */

import type { PublishActions } from "@mari-design/components/pages/PublishPage";
import { gql } from "../../lib/api";
import { mutate, type ActionContext } from "./index";
import { botActions } from "./bots";

const SITES = `{ sites { id name status theme } }`;
const RELEASES = `{ releases { id siteId version } }`;
const SERVERS = `{ mcpServers { id name url } }`;
const SETTINGS = `{ settings { key value } }`;

const DEPLOY = `mutation($id: Int!) { deploySite(id: $id) }`;
const BUILD = `mutation($id: Int!) { buildSite(id: $id) }`;
const ROLLBACK = `mutation($id: Int!) { rollbackRelease(id: $id) }`;
const SET_FEATURE = `mutation($id: Int!, $key: String!, $on: Boolean!) { setSiteFeature(id: $id, key: $key, on: $on) }`;
const SET_THEME = `mutation($id: Int!, $theme: JSON!) { updateSiteTheme(id: $id, theme: $theme) }`;
const UPDATE_SETTING = `mutation($key: String!, $value: JSON!) { updateSetting(key: $key, value: $value) }`;
const CREATE_SITE = `mutation($name: String!, $domain: String!, $sources: JSON!) {
  createSite(name: $name, domain: $domain, sources: $sources)
}`;
const CREATE_SERVER = `mutation($name: String!, $scope: String!, $capabilities: JSON!) {
  createMcpServer(name: $name, scope: $scope, capabilities: $capabilities)
}`;
const UPDATE_SERVER = `mutation($id: Int!, $scope: String, $capabilities: JSON!) {
  updateMcpServer(id: $id, scope: $scope, capabilities: $capabilities)
}`;
const DELETE_SERVER = `mutation($id: Int!) { deleteMcpServer(id: $id) }`;
const TEST_SERVER = `mutation($id: Int!) { testMcpServer(id: $id) }`;

type SiteRow = { id: number; name: string; status: string; theme: Record<string, unknown> | null };

/** The site this page is editing: the one the route names. Throws rather than
 *  guessing when the route names none — a deploy that silently went to a
 *  different site than the one on screen is worse than a message. */
async function currentSite(): Promise<SiteRow> {
  const asked = Number(new URLSearchParams(window.location.search).get("site"));
  const res = await gql<{ sites: SiteRow[] }>(SITES);
  const site = (res?.sites ?? []).find((s) => s.id === asked);
  if (!site) throw new Error("That doc site is no longer in this workspace.");
  return site;
}

export function publishActions({ navigate }: ActionContext): PublishActions {
  return {
    ...botActions(),
    /* The top-level tab goes in the URL, so a Publish tab survives a reload
       and can be linked — `web/src/data/publish.ts` reads `?tab` straight back
       out. Switching to MCP leaves whichever site was open, because the MCP
       half is not about a site. */
    openSection: (section) => navigate(section === "sites" ? "/publish" : `/publish?tab=${section}`),
    // "All sites" is the Publish page with no site selected.
    openSites: () => navigate("/publish"),
    openSite: (id: number) => navigate(`/publish?site=${id}`),
    /* Creating a site is where its sources are decided — the editor treats
       them as fixed — and the new site is where the user then is. */
    createSite: async ({ name, domain, sourceTags }) => {
      const d = await mutate(CREATE_SITE, { name, domain, sources: sourceTags });
      const id = d?.createSite;
      if (typeof id !== "number" || id <= 0) {
        throw new Error(`"${name}" could not be created. A site with that name may already exist.`);
      }
      navigate(`/publish?site=${id}`);
    },
    /* ── the doc site ─────────────────────────────────────────────────────*/
    deploySite: async () => {
      const site = await currentSite();
      await mutate(DEPLOY, { id: site.id });
    },
    // "Preview build" builds without releasing, which is what buildSite does.
    buildSite: async () => {
      const site = await currentSite();
      await mutate(BUILD, { id: site.id });
    },
    rollbackRelease: async (version) => {
      const site = await currentSite();
      const res = await gql<{ releases: { id: number; siteId: number; version: string }[] }>(RELEASES);
      const rel = (res?.releases ?? []).find((r) => r.siteId === site.id && r.version === version);
      if (!rel) throw new Error(`Release ${version} is no longer in this site's history.`);
      await mutate(ROLLBACK, { id: rel.id });
    },
    setSiteFeature: async (key, on) => {
      const site = await currentSite();
      await mutate(SET_FEATURE, { id: site.id, key, on });
    },
    setSiteTheme: async ({ preset, accent }) => {
      const site = await currentSite();
      // The theme row also holds fields this tab does not edit (fonts,
      // density), and updateSiteTheme replaces it whole, so it is merged.
      const theme = { ...(site.theme ?? {}) } as Record<string, unknown>;
      if (preset !== undefined) theme.preset = preset;
      if (accent !== undefined) theme.accent = accent;
      await mutate(SET_THEME, { id: site.id, theme });
    },
    saveDeployConfig: async ({ bucket, region }) => {
      const res = await gql<{ settings: { key: string; value: unknown }[] }>(SETTINGS);
      const cur = (res?.settings ?? []).find((s) => s.key === "deploy")?.value;
      const row = cur && typeof cur === "object" ? { ...(cur as Record<string, unknown>) } : {};
      await mutate(UPDATE_SETTING, { key: "deploy", value: { ...row, bucket, region } });
    },

    /* ── MCP servers ──────────────────────────────────────────────────────*/
    createServer: async ({ name, scope, capabilities }) => {
      // The token exists only in this response, and only once. It goes
      // straight to the page's TokenReveal; nothing here keeps or logs it.
      const d = await mutate(CREATE_SERVER, { name, scope, capabilities });
      const token = d?.createMcpServer;
      if (typeof token !== "string" || !token) {
        throw new Error("The server was created but returned no bearer token to show.");
      }
      // The id and the URL are the server's to mint, so they are read back
      // rather than invented: the card's Copy button must hand out the real
      // endpoint, not a plausible-looking one.
      const res = await gql<{ mcpServers: { id: number; name: string; url: string }[] }>(SERVERS);
      const row = (res?.mcpServers ?? []).find((s) => s.name === name);
      if (!row) throw new Error("The server was created but is not in the list yet.");
      return { id: row.id, url: row.url, token };
    },
    updateServer: async (id, { scope, capabilities }) => {
      await mutate(UPDATE_SERVER, { id, scope, capabilities });
    },
    deleteServer: async (id) => {
      await mutate(DELETE_SERVER, { id });
    },
    testServer: async (id) => {
      const d = await mutate(TEST_SERVER, { id });
      return (d?.testMcpServer ?? {}) as { ok?: boolean; latency_ms?: number; checks?: Record<string, number> };
    },
  };
}
