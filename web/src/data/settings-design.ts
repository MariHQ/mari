/* Settings → Design & brand adapter.
 *
 * The brand lives in the `settings` table under the `branding` key, which is
 * the same place `updateSetting` writes and the site builder reads — so what
 * you save here is what a published doc site picks up.
 */

import type { SettingsDesignData } from "@mari-design/components/pages/SettingsDesignPage";
import type { Branding, BrandHarvest, BrandPreviewStat } from "@mari-design/components/features/BrandingEditor";
import type { PropertyItem } from "@mari-design/components/data-display/PropertyList";
import { useMemo } from "react";
import { useQuery } from "../lib/api";
import type { PageData } from "./types";

const QUERY = `query SettingsDesign {
  settings { key value }
  sites { id status }
  overviewStats
}`;

type Res = {
  settings: { key: string; value: any }[];
  sites: { id: number; status: string }[];
  overviewStats: Record<string, number> | null;
};

/** The import evidence panel is only ever filled by a real import, so the
    fallback is empty rather than a fabricated palette. */
const NO_HARVEST: BrandHarvest = {
  title: "",
  themeColor: "",
  cssColors: [],
  fonts: [],
  logo: null,
  warnings: [],
};

/** Pure: settings rows + a couple of counts → everything the page renders. */
export function buildSettingsDesign(res: Res | null): SettingsDesignData {
  const rows = res?.settings ?? [];
  const raw = rows.find((r) => r.key === "branding")?.value;
  const value = typeof raw === "string" ? safeParse(raw) : raw;
  const branding: Branding = value && typeof value === "object" ? value : {};

  const live = (res?.sites ?? []).filter((s) => s.status === "live").length;
  const docs = res?.overviewStats?.documents ?? 0;

  // The preview shows off THIS workspace, so its figures come from the same
  // counts the rest of the console uses rather than invented numbers.
  const previewStats: BrandPreviewStat[] = [
    { value: docs ? docs.toLocaleString() : "0", label: "documents" },
    { value: String((res?.sites ?? []).length), label: "doc sites" },
    { value: String(live), label: "live" },
  ];

  const summary: PropertyItem[] = [
    { label: "Doc sites", value: live ? `${live} live` : "None live yet" },
    { label: "Applies to", value: "Published doc sites and exports" },
  ];

  return { branding, harvest: NO_HARVEST, previewStats, summary };
}

function safeParse(s: string): unknown {
  try { return JSON.parse(s); } catch { return null; }
}

export function useSettingsDesign(): PageData<SettingsDesignData> {
  const q = useQuery<Res>(QUERY);
  /* Memoised on the raw answer, which `useQuery` holds in state and so keeps
     referentially stable. Without this, `buildSettingsDesign` minted a fresh
     `branding` object on every render — and `BrandingEditor`'s resync sentinel
     compares identity, so it would have adopted "new" branding on every
     keystroke and thrown away the brand the reader was in the middle of
     editing (C1). */
  const data = useMemo(() => buildSettingsDesign(q.data), [q.data]);
  return {
    data,
    loading: q.loading,
    // `q.error` is a boolean; the page wants the message, with a floor for a
    // failure that carried none.
    error: q.error ? (q.errorText ?? "Branding settings are temporarily unavailable.") : null,
  };
}
