/* Settings → Members actions — who can reach this workspace, and how.
 *
 * Every handler maps one for one onto a mutation. Removing a member is
 * destructive and the page routes it through <ConfirmButton>, so nothing here
 * fires on a first click. */

import type { SettingsMembersActions } from "@mari-design/components/pages/SettingsMembersPage";
import { mutate } from "./index";

const INVITE = `mutation($name: String!, $email: String!, $role: String!) {
  inviteMember(name: $name, email: $email, role: $role)
}`;
const SET_ROLE = `mutation($id: Int!, $role: String!) { setMemberRole(id: $id, role: $role) }`;
const REMOVE = `mutation($id: Int!) { removeMember(id: $id) }`;
const SET_WORKSPACE_NAME = `mutation($name: String!) { setWorkspaceName(name: $name) }`;
const SET_GITHUB_TEAM = `mutation($team: String!) { setGithubTeam(team: $team) }`;

export function settingsMembersActions(): SettingsMembersActions {
  return {
    inviteMember: async ({ name, email, role }) => {
      await mutate(INVITE, { name, email, role });
    },
    setRole: async (id, role) => {
      await mutate(SET_ROLE, { id, role });
    },
    removeMember: async (id) => {
      await mutate(REMOVE, { id });
    },
    setWorkspaceName: async (name) => {
      await mutate(SET_WORKSPACE_NAME, { name });
    },
    setGithubTeam: async (team) => {
      await mutate(SET_GITHUB_TEAM, { team });
    },
  };
}
