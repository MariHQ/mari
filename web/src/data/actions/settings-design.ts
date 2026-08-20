/* Design & brand writes.
 *
 * Workspace branding is explicit user-owned configuration. No remote-site
 * scraper or inferred brand mutation sits behind this form.
 */

import type { BrandingEditorActions } from "@mari-design/components/features/BrandingEditor";
import { mutate } from "./index";

const SAVE = `mutation SaveBranding($value: JSON!) {
  updateSetting(key: "branding", value: $value)
}`;

export function settingsDesignActions(): BrandingEditorActions {
  return {
    save: async (branding) => { await mutate(SAVE, { value: branding }); },
  };
}
