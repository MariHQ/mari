/* Page id → adapter. The one place the app decides where a page's data comes
 * from. Anything absent here falls back to the stub (see stubs.ts). */

import { useAudit } from "./audit";
import { useSettingsAuditLog } from "./audit-log";
import { useLogin, useSetup } from "./auth-pages";
import { useDecisions } from "./decisions";
import { useDocReview } from "./doc-review";
import { useFacts } from "./facts";
import { useInsights } from "./insights";
import { useKnowledge } from "./knowledge";
import { useLibrary } from "./library";
import { useLineage } from "./lineage";
import { useOverview } from "./overview";
import { usePreferences } from "./preferences";
import { usePublish } from "./publish";
import { useSettingsApiKeys, useSettingsMembers } from "./settings";
import { useSettingsDesign } from "./settings-design";
import { useSettingsGeneral } from "./settings-general";
import { useSettingsModels } from "./settings-models";
import { useSources } from "./sources";
import { stub } from "./stubs";
import { useWorkflows } from "./workflows";
import type { Adapter } from "./types";
import { useWelcome } from "./welcome";

const ADAPTERS: Record<string, Adapter<any>> = {
  audit: useAudit,
  decisions: useDecisions,
  "doc-review": useDocReview,
  facts: useFacts,
  insights: useInsights,
  knowledge: useKnowledge,
  library: useLibrary,
  lineage: useLineage,
  login: useLogin,
  overview: useOverview,
  preferences: usePreferences,
  publish: usePublish,
  setup: useSetup,
  "settings-api-keys": useSettingsApiKeys,
  "settings-audit-log": useSettingsAuditLog,
  "settings-design": useSettingsDesign,
  "settings-general": useSettingsGeneral,
  "settings-members": useSettingsMembers,
  "settings-models": useSettingsModels,
  sources: useSources,
  welcome: useWelcome,
  workflows: useWorkflows,
};

export function adapterFor(pageId: string): Adapter<any> {
  return ADAPTERS[pageId] ?? stub;
}
