/* Settings → General actions — the workspace identity record.
 *
 * One intent: save the form. It lands in two places because the server keeps
 * it in two places, and the page should not have to know that. */

import type { LineageTuning, SettingsGeneralActions, WorkspaceIdentity } from "@mari-design/components/pages/SettingsGeneralPage";
import { gql } from "../../lib/api";
import { mutate } from "./index";

const SET_NAME = `mutation($name: String!) { setWorkspaceName(name: $name) }`;
const UPDATE_SETTING = `mutation($key: String!, $value: JSON!) { updateSetting(key: $key, value: $value) }`;
const WORKSPACE_ROW = `{ settings { key value } }`;

/** The `workspace` settings row as it stands right now. `updateSetting`
 *  REPLACES a row wholesale, so anything this form does not edit has to be
 *  read back and carried across, or saving a timezone would quietly drop
 *  every other key the row holds. */
async function currentWorkspaceRow(): Promise<Record<string, unknown>> {
  const res = await gql<{ settings: { key: string; value: unknown }[] }>(WORKSPACE_ROW);
  const row = (res?.settings ?? []).find((s) => s.key === "workspace")?.value;
  return row && typeof row === "object" ? { ...(row as Record<string, unknown>) } : {};
}

export function settingsGeneralActions(): SettingsGeneralActions {
  return {
    saveWorkspace: async (w: WorkspaceIdentity) => {
      // The name has its own mutation because the server does more with it
      // than store a string; the other four are plain fields of the same row.
      await mutate(SET_NAME, { name: w.name });
      const row = await currentWorkspaceRow();
      await mutate(UPDATE_SETTING, {
        key: "workspace",
        value: {
          ...row,
          name: w.name, slug: w.slug, plan: w.plan,
          timezone: w.timezone, language: w.language,
        },
      });
    },
    saveLineageTuning: async (tuning: LineageTuning) => {
      await mutate(UPDATE_SETTING, {
        key: "lineage",
        value: { max_nodes: tuning.maxNodes, hop_depth: tuning.hopDepth },
      });
    },
    // No handler for the danger zone: transferring or deleting a workspace has
    // no mutation, and the page leaves those controls local rather than
    // pointing them at an endpoint that does not exist. `data.danger` is false
    // in this app anyway, so they are not even offered.
  };
}
