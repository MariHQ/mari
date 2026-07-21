// Shared bits for the Settings pages: settings map + save helpers.
// Page headers come from <PageHeader eyebrow="Settings" …/> (ui) — the old
// SetHead is gone. MONO/HINT style objects are gone too: use <code> for mono
// spans and Card hint / Field hint slots (or .card__hint / .field__hint) for
// muted copy.

import { useEffect, useRef, useState } from "react";
import * as Ic from "../../components/icons";
import { gql, useQuery, type QueryResult } from "../../lib/api";

export type SettingsMap = Record<string, any>;

const SETTINGS_Q = `{ settings { key value } }`;
const mapSettings = (d: any): SettingsMap =>
  Object.fromEntries((d.settings ?? []).map((s: any) => [s.key, s.value]));

/** Full query state for callers that want an honest loading/error signal. */
function useSettingsQuery(): QueryResult<SettingsMap> {
  return useQuery<SettingsMap>(SETTINGS_Q, { map: mapSettings });
}

/** The settings map — `{}` while loading (no canned fallback data). Bump
 *  `nonce` to refetch after a save. Pair with useSettingsQuery() when the
 *  consuming UI needs the loading state itself. */
export function useSettings(nonce = 0): SettingsMap {
  const { data, refetch } = useSettingsQuery();
  const first = useRef(true);
  useEffect(() => {
    if (first.current) { first.current = false; return; }
    refetch();
  }, [nonce, refetch]);
  return data ?? {};
}

export async function saveSetting(key: string, value: unknown): Promise<boolean> {
  const r = await gql(
    `mutation($key: String!, $value: JSON!) { updateSetting(key: $key, value: $value) }`,
    { key, value },
  );
  return !!r;
}

/** Small saving / saved-flash state machine for slow buttons. */
export function useSave() {
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const run = async (fn: () => Promise<unknown>) => {
    setSaving(true);
    setSaved(false);
    try {
      await fn();
    } finally {
      setSaving(false);
      setSaved(true);
      window.setTimeout(() => setSaved(false), 1800);
    }
  };
  return { saving, saved, run };
}

/** The uniform post-save flash for all Settings pages (canonical .saved-note). */
export function SavedNote({ children = "Saved" }: { children?: React.ReactNode }) {
  return (
    <span className="saved-note">
      <Ic.Check size={13} /> {children}
    </span>
  );
}

export const cap = (s: string) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);
