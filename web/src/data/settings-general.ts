/* Settings → General adapter — the workspace identity record.
 *
 * Everything on this page comes out of the `workspace` settings row plus two
 * counts for the rail. The save lifecycle and the slug rejection belong to a
 * mutation, so a freshly loaded form is clean and valid. */

import type { SettingsGeneralData } from "@mari-design/components/pages/SettingsGeneralPage";
import type { PropertyItem } from "@mari-design/components";
import { useMemo } from "react";
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

/* Branding is no longer sent from here. The editor moved to Settings →
   Design & brand, which is the only place it has handlers, and General stopped
   reading `branding` / `brandHarvest` / `brandPreviewStats` — so this page no
   longer ships three empty records to a section that does not draw them. */

/* ── mapper ─────────────────────────────────────────────────────────────── */

export const EMPTY: SettingsGeneralData = {
  section: "workspace",
  name: "", slug: "", plan: "", timezone: "", language: "",
  save: "clean", slugError: null, summary: [], danger: false,
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
    // Passed through as stored. The form's options are IANA zone ids now, and
    // it reads a legacy "utc"/"pt"/"et" transparently — so a row written before
    // the change still selects the right option, and the next save replaces it
    // with the real zone rather than the console rewriting history on read.
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
  };
}

/* ── adapter ────────────────────────────────────────────────────────────── */

export function useSettingsGeneral(): PageData<SettingsGeneralData> {
  const q = useQuery<Res>(QUERY, { map: (d: Res) => d });
  /* Built once per RESPONSE, not once per render. SettingsGeneralPage seeds
     its whole edit buffer from this object and resyncs on `seen !== data` —
     comparing the OBJECT, not its fields — so a mapper that returned a fresh
     object every render would reset name/slug/plan/timezone/language on every
     keystroke. */
  const data = useMemo(() => buildSettingsGeneral(q.data), [q.data]);
  return {
    data,
    loading: q.loading,
    error: q.error ? (q.errorText ?? "Workspace settings are temporarily unavailable.") : null,
  };
}
