/* Settings → General adapter — the workspace identity record.
 *
 * Everything on this page comes out of the `workspace` settings row plus two
 * counts for the rail. The save lifecycle and the slug rejection belong to a
 * mutation, so a freshly loaded form is clean and valid. */

import type { SettingsGeneralData } from "@mari-design/components/pages/SettingsGeneralPage";
import type { Branding, BrandHarvest, BrandPreviewStat } from "@mari-design/components/features/BrandingEditor";
import type { PropertyItem } from "@mari-design/components";
import { useQuery } from "../lib/api";
import type { PageData } from "./types";

/* ── query ──────────────────────────────────────────────────────────────── */

const QUERY = `{
  workspace { name slug plan timezone language }
  provisioning { ssoProviders ssoEnabled scimStatus githubTeam { team connected syncedMembers } }
  members { id status }
  graphStats { docs }
}`;

type Res = {
  workspace: { name: string; slug: string; plan: string; timezone: string; language: string } | null;
  provisioning: {
    ssoProviders: string[]; ssoEnabled: boolean; scimStatus: string;
    githubTeam: { team: string; connected: boolean; syncedMembers: number };
  } | null;
  members: { id: number; status: string }[];
  graphStats: { docs: number } | null;
};

type WorkspaceRow = { name?: string; slug?: string; plan?: string; timezone?: string; language?: string };

/* ── mapping helpers ────────────────────────────────────────────────────── */

/** The `workspace` settings row, as the server resolves it. Every field is ""
 *  on a workspace first-run setup has not filled in — a real state, not a
 *  missing fetch. */
function workspaceRow(res: Res): WorkspaceRow {
  return res.workspace ?? {};
}

/** Brand identity has no settings row: `importBrand` harvests a candidate for
 *  the editor, and nothing persists it yet. Every field is absent, so the
 *  editor falls back to its own defaults instead of showing someone's colors. */
const NO_BRANDING: Branding = {};
const NO_HARVEST: BrandHarvest = {
  title: "", themeColor: "", cssColors: [], fonts: [], logo: null, warnings: [],
};

/* ── mapper ─────────────────────────────────────────────────────────────── */

export const EMPTY: SettingsGeneralData = {
  section: "workspace",
  name: "", slug: "", plan: "", timezone: "", language: "",
  save: "clean", slugError: null, summary: [], danger: false,
  branding: NO_BRANDING, brandHarvest: NO_HARVEST, brandPreviewStats: [],
};

/** Pure: the whole response → everything the page renders. */
export function buildSettingsGeneral(res: Res | null): SettingsGeneralData {
  if (!res) return EMPTY;
  const ws = workspaceRow(res);
  const members = res.members ?? [];
  const active = members.filter((m) => m.status === "active").length;
  const invited = members.filter((m) => m.status === "invited").length;

  // The rail's read-only facts are counted off the same rows the Members tab
  // renders, so the two can never disagree. A fact with no source (region,
  // creation date) is simply not a row.
  const prov = res.provisioning;
  const summary: PropertyItem[] = [
    ...(ws.plan ? [{ label: "Plan", value: ws.plan }] : []),
    ...(members.length ? [{ label: "Members", value: `${active} active, ${invited} invited` }] : []),
    ...(res.graphStats ? [{ label: "Documents", value: res.graphStats.docs.toLocaleString("en-US") }] : []),
    // How people get in, as the server reports it actually being configured.
    // A team nobody set up is not a row; "connected" is the server's own
    // judgement (a team AND a credential to read it with).
    ...(prov?.githubTeam?.team
      ? [{
          label: "GitHub team",
          value: prov.githubTeam.connected
            ? `${prov.githubTeam.team} · ${prov.githubTeam.syncedMembers} synced`
            : `${prov.githubTeam.team} · no credential`,
          stacked: true,
        }]
      : []),
    ...(prov?.ssoEnabled ? [{ label: "Single sign-on", value: prov.ssoProviders.join(", ") }] : []),
  ];

  return {
    section: "workspace",
    name: ws.name ?? "",
    slug: ws.slug ?? "",
    plan: ws.plan ?? "",
    timezone: ws.timezone ?? "",
    language: ws.language ?? "",
    // A form that was just loaded matches the server and has been rejected by
    // nobody. Both change the moment this app wires up updateSetting.
    save: "clean",
    slugError: null,
    summary,
    // The destructive controls are owner-only and this app has no ownership
    // check yet, so they stay off rather than being offered to everyone.
    danger: false,
    branding: NO_BRANDING,
    brandHarvest: NO_HARVEST,
    // The branded preview's figures would be a second, unowned copy of the
    // corpus stats. Empty until Publish and Insights agree on one source.
    brandPreviewStats: [] as BrandPreviewStat[],
  };
}

/* ── adapter ────────────────────────────────────────────────────────────── */

export function useSettingsGeneral(): PageData<SettingsGeneralData> {
  const q = useQuery<Res>(QUERY, { map: (d: Res) => d });
  return {
    data: buildSettingsGeneral(q.data),
    loading: q.loading,
    error: q.error ? (q.errorText ?? "Workspace settings are temporarily unavailable.") : null,
  };
}
