/* Preferences adapter.
 *
 * Like the auth pages, this reads REST rather than GraphQL: an account's own
 * profile is not workspace knowledge, it is session state, and it lives behind
 * `/auth/preferences` with the session cookie as the whole authorization
 * story. There is deliberately no user id anywhere in this file.
 */

import { useCallback, useEffect, useState } from "react";
import type { PreferencesData, AuthProvider } from "@mari-design/components/pages/PreferencesPage";
import type { PropertyItem } from "@mari-design/components/data-display/PropertyList";
import { fmtDate } from "@mari-design/components/tokens/format";
import type { PageData } from "./types";

type PrefsResponse = {
  name: string;
  email: string;
  initials: string;
  role: string;
  joined: string;
  provider: string;
  timezone: string;
  notifications: { mentions: boolean; digest: boolean; flowFailures: boolean };
};

/** Blank account, for the moment before the first response lands. The page is
    a pure presenter and requires `data`, so this names the empty state rather
    than inventing a person to fill it. */
const EMPTY: PreferencesData = {
  profile: { name: "", email: "", initials: "", timezone: "UTC" },
  provider: "password",
  notifications: { mentions: false, digest: false, flowFailures: false },
  summary: [],
};

/** The API's provider string, narrowed to what the page understands. Anything
    unrecognised falls back to `password`, which is the conservative choice: it
    offers the password form rather than hiding it from someone who has one. */
function providerOf(raw: string): AuthProvider {
  return raw === "github" || raw === "google" ? raw : "password";
}

const ROLE_LABEL: Record<string, string> = {
  admin: "Admin",
  manager: "Manager",
  user: "Member",
};

/** Pure: the API response → everything the page renders. */
export function buildPreferences(r: PrefsResponse): PreferencesData {
  const summary: PropertyItem[] = [
    { label: "Role", value: ROLE_LABEL[r.role] ?? r.role },
    { label: "Sign-in", value: providerOf(r.provider) === "password" ? "Email and password" : providerOf(r.provider) === "github" ? "GitHub" : "Google" },
  ];
  // Only shown when the server actually recorded a join date — an empty row
  // reads as missing data, which is exactly what it would be. The API returns
  // ISO and the client formats it: "2024-04-01" in a rail that reads
  // "Apr 1, 2024" everywhere else is the API's format leaking out.
  if (r.joined) summary.push({ label: "Member since", value: fmtDate(r.joined) });

  return {
    profile: {
      name: r.name,
      email: r.email,
      initials: r.initials,
      timezone: r.timezone || "UTC",
    },
    provider: providerOf(r.provider),
    notifications: r.notifications,
    summary,
  };
}

export function usePreferences(): PageData<PreferencesData> {
  const [data, setData] = useState<PreferencesData>(EMPTY);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch("/auth/preferences");
      if (!res.ok) throw new Error(`The API answered ${res.status}.`);
      setData(buildPreferences(await res.json()));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not reach the API.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  return { data, loading, error };
}
