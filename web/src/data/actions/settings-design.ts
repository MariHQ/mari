/* Design & brand writes.
 *
 * Both halves already existed on the server and neither was reachable from the
 * UI: `importBrand` harvests a palette and fonts off a real homepage, and
 * `updateSetting` persists arbitrary settings rows. The editor's Save and
 * Import were local state, so a brand you set was forgotten on reload.
 */

import type { BrandingEditorActions } from "@mari-design/components/features/BrandingEditor";
import type { BrandHarvest } from "@mari-design/components/features/BrandingEditor";
import { mutate } from "./index";

const SAVE = `mutation SaveBranding($value: JSON!) {
  updateSetting(key: "branding", value: $value)
}`;

const IMPORT = `mutation ImportBrand($url: String!) {
  importBrand(url: $url)
}`;

/** The mutation answers with data rather than throwing, even for a failed
    fetch — `{error, warnings}` instead of a 500 — so a failure has to be
    recognised here and turned into one the editor can show. */
function toHarvest(raw: any): BrandHarvest {
  if (raw?.error) throw new Error(String(raw.error));
  const colors = Array.isArray(raw?.cssColors) ? raw.cssColors : [];
  return {
    title: String(raw?.title ?? ""),
    themeColor: String(raw?.themeColor ?? raw?.accent ?? ""),
    // [hex, weight] pairs; anything malformed is dropped rather than rendered
    // as an undefined swatch.
    cssColors: colors
      .filter((c: unknown) => Array.isArray(c) && typeof c[0] === "string")
      .map((c: [string, number]) => [c[0], Number(c[1]) || 0] as [string, number]),
    fonts: (Array.isArray(raw?.fonts) ? raw.fonts : []).filter((f: unknown) => typeof f === "string"),
    logo: typeof raw?.logo === "string" ? raw.logo : null,
    warnings: (Array.isArray(raw?.warnings) ? raw.warnings : []).map(String),
  };
}

export function settingsDesignActions(): BrandingEditorActions {
  return {
    save: async (branding) => { await mutate(SAVE, { value: branding }); },
    importFrom: async (url: string) => toHarvest((await mutate(IMPORT, { url }))?.importBrand),
  };
}
