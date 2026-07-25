/* Settings adapters — Members and API keys.
 *
 * Both pages carry a lot of interaction state (which menu is open, what is in
 * the invite form, which row a confirmation is pending on). That is local UI
 * state, not workspace content: the pages take it as data so the design canvas
 * can drive every lifecycle step through the same rendering path, and an app
 * drives it from its own `useState`. Only the collections come from the API. */

import { useMemo, useState } from "react";
import type { PropertyItem } from "@mari-design/components";
import type { SettingsApiKeysData } from "@mari-design/components/pages/SettingsApiKeysPage";
import type { ApiKey } from "@mari-design/components/features/SettingsApiKeys";
import type { SettingsMembersData } from "@mari-design/components/pages/SettingsMembersPage";
import type { GithubTeamSync, Member } from "@mari-design/components/features/SettingsMembersTable";
import { useQuery } from "../lib/api";
import type { PageData } from "./types";

/* ── Members ────────────────────────────────────────────────────────────── */

const MEMBERS_QUERY = `{
  members { id name initials email role status joined }
  workspace { name }
  provisioning { githubTeam { team connected } }
}`;

type MembersRes = {
  members: { id: number; name: string; initials: string; email: string; role: string; status: string; joined: string }[];
  workspace: { name: string } | null;
  provisioning: { githubTeam: { team: string; connected: boolean } } | null;
};

export function mapMembers(res: MembersRes): Member[] {
  return (res.members ?? []).map((m) => ({
    id: m.id, name: m.name, initials: m.initials, email: m.email,
    role: m.role as Member["role"], status: m.status, joined: m.joined,
  }));
}

/** The team members are auto-provisioned from. `connected` is the server's
 *  own judgement — a team is configured AND there is a credential to read it
 *  with — so the card cannot claim a sync it could not perform. */
export function mapGithubTeam(res: MembersRes): GithubTeamSync {
  const gh = res.provisioning?.githubTeam;
  return { connected: Boolean(gh?.connected), team: gh?.team ?? "" };
}

/** Pure: roster + workspace identity + local interaction state → everything
 *  the page renders. */
export function buildMembers(
  members: Member[],
  workspaceName: string,
  githubTeam: GithubTeamSync,
  interaction: SettingsMembersData["interaction"],
): SettingsMembersData {
  // The rail's read-only facts are counted off the roster we already have —
  // derived, not a second source that could disagree with the table.
  const summary: PropertyItem[] = members.length
    ? [
        { label: "Members", value: String(members.length) },
        { label: "Admins", value: String(members.filter((m) => m.role === "admin").length) },
        { label: "Pending invites", value: String(members.filter((m) => m.status === "invited").length) },
      ]
    : [];

  return {
    members,
    // From the `workspace` settings row and the provisioning record. An
    // unnamed workspace and an unconfigured team are real states, and both
    // render as "not configured" rather than as a guess.
    workspaceName,
    githubTeam,
    summary,
    interaction,
    invite: { name: "", email: "", role: "user" },
    focusMemberId: null,
  };
}

export function useSettingsMembers(): PageData<SettingsMembersData> {
  const [interaction] = useState<SettingsMembersData["interaction"]>("none");
  const q = useQuery<MembersRes>(MEMBERS_QUERY, { map: (d: MembersRes) => d });
  /* Built once per RESPONSE, not once per render. SettingsMembersTable
     resyncs its roster on `seenMembers !== members` (array identity), so
     re-running mapMembers every render would throw away every optimistic
     role change the moment the next render happened. */
  const data = useMemo(
    () => buildMembers(
      q.data ? mapMembers(q.data) : [],
      q.data?.workspace?.name ?? "",
      q.data ? mapGithubTeam(q.data) : { connected: false, team: "" },
      interaction,
    ),
    [q.data, interaction],
  );
  return {
    data,
    loading: q.loading,
    error: q.error ? (q.errorText ?? "The member list is temporarily unavailable.") : null,
  };
}

/* ── API keys ───────────────────────────────────────────────────────────── */

const KEYS_QUERY = `{ apiKeys { id name prefix scopes created lastUsed revoked } }`;

type KeysRes = {
  apiKeys: { id: number; name: string; prefix: string; scopes: string; created: string; lastUsed: string; revoked: boolean }[];
};

export function mapApiKeys(res: KeysRes): ApiKey[] {
  return (res.apiKeys ?? []).map((k) => ({
    id: k.id, name: k.name, prefix: k.prefix, scopes: k.scopes, created: k.created,
    // The page distinguishes "never used" from a date, so an empty string from
    // the API has to become null rather than an empty cell.
    lastUsed: k.lastUsed || null,
    revoked: k.revoked,
  }));
}

/** Pure: key list + local lifecycle phase → everything the page renders. */
export function buildApiKeys(keys: ApiKey[], phase: SettingsApiKeysData["phase"]): SettingsApiKeysData {
  const summary: PropertyItem[] = keys.length
    ? [
        { label: "Keys", value: String(keys.length) },
        { label: "Active", value: String(keys.filter((k) => !k.revoked).length) },
        { label: "Revoked", value: String(keys.filter((k) => k.revoked).length) },
      ]
    : [];

  return {
    phase,
    keys,
    draft: { name: "", scopes: "" },
    // The secret exists only in the createApiKey mutation's response, and
    // only once. Nothing to show on a plain list load.
    newSecret: null,
    confirmKeyId: null,
    summary,
  };
}

export function useSettingsApiKeys(): PageData<SettingsApiKeysData> {
  const [phase] = useState<SettingsApiKeysData["phase"]>("list");
  const q = useQuery<ApiKey[]>(KEYS_QUERY, { map: mapApiKeys });
  /* Built once per RESPONSE, not once per render. SettingsApiKeys resyncs its
     list on `seenKeys !== keys`, and `q.data ?? []` mints a NEW empty array
     every render while the query is in flight or has failed — which fires the
     sentinel forever on a workspace with no keys. The `?? []` still means
     exactly what it did (no keys read yet); `loading`/`error` below are what
     tell the page whether that is an answer or an absence. */
  const data = useMemo(() => buildApiKeys(q.data ?? [], phase), [q.data, phase]);
  return {
    data,
    loading: q.loading,
    error: q.error ? (q.errorText ?? "API keys are temporarily unavailable.") : null,
  };
}
